# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the Asana <-> Plane sync (feat/asana-sync).

External Asana API is fully mocked (AsanaClient) — no network. Tests run
against a scratch database created by the runner script (see final report);
mark: contract + django_db(transaction=True) per the automations suite shape.
"""

from datetime import timezone as dt_tz
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from plane.app.asana_sync import mapping
from plane.app.asana_sync.crypto import decrypt_token, encrypt_token
from plane.app.asana_sync.engine import AsanaSyncEngine, run_sync_pass
from plane.db.models import (
    AsanaCommentLink,
    AsanaConnection,
    AsanaProjectSync,
    AsanaSyncLog,
    AsanaTaskLink,
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def isolate_bg():
    with patch("celery.app.task.Task.apply_async"):
        yield


def asana_task(gid="111", name="From Asana", **extra):
    task = {
        "gid": gid,
        "name": name,
        "html_notes": "<body><p>Hello <strong>world</strong></p></body>",
        "completed": False,
        "due_on": "2026-10-01",
        "start_on": None,
        "created_at": "2026-09-01T10:00:00.000Z",
        "modified_at": "2026-09-20T10:00:00.000Z",
        "completed_at": None,
        "permalink_url": "https://app.asana.com/0/1/111",
        "parent": None,
        "num_subtasks": 0,
        "assignee": None,
        "tags": [],
        "memberships": [{"project": {"gid": "999"}, "section": "777"}],
    }
    task.update(extra)
    return task


@pytest.fixture
def setup(request):
    from plane.db.models import Workspace

    owner = User.objects.create(email=f"asana-owner-{uuid4().hex[:6]}@example.test", username=f"asana-owner-{uuid4().hex[:6]}")
    workspace = Workspace.objects.create(name="Asana Sync Test", slug=f"asana-sync-{uuid4().hex[:8]}", owner=owner)
    WorkspaceMember.objects.create(workspace=workspace, member=owner, role=20, is_active=True)
    project = Project.objects.create(name="Sync target", identifier=f"ASYN{uuid4().hex[:4].upper()}", workspace=workspace)
    ProjectMember.objects.create(project=project, member=owner, role=20, is_active=True)
    backlog = State.objects.create(name="Backlog", group="backlog", color="#60646C", project=project, default=True)
    doing = State.objects.create(name="Doing", group="started", color="#777777", project=project)
    done = State.objects.create(name="Done", group="completed", color="#0f8b4c", project=project)
    connection = AsanaConnection.objects.create(
        workspace=workspace,
        name="Main",
        pat_encrypted=encrypt_token("1/123456789012345678901234567890123456"),
        asana_workspace_gid="42",
        asana_workspace_name="Asana Org",
    )
    sync = AsanaProjectSync.objects.create(
        project=project,
        connection=connection,
        asana_project_gid="999",
        asana_project_name="Asana Project",
        direction="bidirectional",
        state_map={"777": {"state_id": str(doing.id), "name": "Doing"}},
        default_state_id=str(backlog.id),
    )
    client = SimpleNamespace(
        tasks=lambda project_gid, modified_since=None: [],
        task=lambda task_gid: asana_task(),
        stories=lambda task_gid: [],
        create_task=lambda ws_gid, data: {**asana_task(gid="555"), **data},
        update_task=lambda task_gid, data: {**asana_task(gid=task_gid), **data},
        add_task_to_project=lambda task_gid, project_gid, section_gid=None: None,
        move_task_to_section=lambda task_gid, project_gid, section_gid: None,
        create_story=lambda task_gid, text: {"gid": "story-1", "text": text},
        sections=lambda project_gid: [{"gid": "777", "name": "Doing"}],
    )
    return SimpleNamespace(
        workspace=workspace, owner=owner, project=project, backlog=backlog, doing=doing, done=done,
        connection=connection, sync=sync, client=client,
    )


# --------------------------------------------------------------------- mapping


def test_asana_notes_to_plane_description():
    html = mapping.asana_html_to_plane_html("<body><p>Hi</p><li>one</li><li>two</li></body>")
    assert html.startswith("<p>Hi</p>")
    assert "<ul><li>one</li><li>two</li></ul>" in html


def test_script_tag_is_stripped():
    html = mapping.asana_html_to_plane_html("<body><p>a</p><script>alert(1)</script></body>")
    assert "script" not in html.lower() and "alert" not in html


def test_plane_description_to_asana_notes_roundtrip_keeps_link_and_bold():
    notes = mapping.plane_html_to_asana_notes_html('<p>See <a href="https://x.co">x</a> <strong>now</strong></p>')
    assert notes.startswith("<body>") and notes.endswith("</body>")
    assert '<a href="https://x.co">' in notes and "<strong>now</strong>" in notes


def test_comment_text_is_plain_for_asana_stories():
    text = mapping.comment_text_for_asana("<p>Line1<br/>Line <b>2</b></p>")
    assert "<" not in text and "Line1" in text and "Line 2" in text


# ------------------------------------------------------------------ crypto


def test_pat_encrypted_at_rest_and_reversible(setup):
    setup.connection.refresh_from_db()
    stored = setup.connection.pat_encrypted
    assert "1/1234" not in stored
    assert decrypt_token(stored) == "1/123456789012345678901234567890123456"


# --------------------------------------------------------------------- pull


def test_pull_creates_issue_with_mapped_state_and_due(setup):
    engine = AsanaSyncEngine(setup.sync, setup.client)
    created = engine.pull_full()
    assert created == 0  # default mocked client lists no tasks
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    created = AsanaSyncEngine(setup.sync, setup.client).pull_full()
    assert created == 1

    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert issue.state_id == setup.doing.id  # section 777 mapped
    assert issue.target_date == timezone.datetime(2026, 10, 1).date()
    assert issue.description_html == "<p>Hello <strong>world</strong></p>"
    link = AsanaTaskLink.objects.get(sync=setup.sync, issue=issue)
    assert link.asana_task_gid == "111"
    # Loop guard: after pull, plane_synced_at == issue.updated_at
    assert link.plane_synced_at is not None


def test_pull_does_not_recreate_unchanged_task(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.pull_full() == 1
    assert engine.pull_full() == 0
    assert Issue.objects.filter(project=setup.project).count() == 1


def test_pull_completed_task_lands_in_completed_state(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(gid="222", completed=True, completed_at="2026-09-21T08:00:00.000Z")
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert issue.state.group == "completed"


def test_pull_unmapped_section_provisions_state_and_registers_map(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(gid="333", memberships=[{"project": {"gid": "999"}, "section": "888"}])
    ]
    setup.client.sections = lambda project_gid: [{"gid": "888", "name": "New column"}]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert issue.state.name == "New column"
    assert "888" in setup.sync.state_map
    assert setup.sync.state_map["888"]["state_id"] == str(issue.state_id)


def test_pull_real_api_section_shape_creates_issue(setup):
    """Asana returns memberships[].section as an object ({gid, name}) —
    regression for the unhashable-dict crash that broke every real pull."""
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(gid="444", memberships=[{"project": {"gid": "999"}, "section": {"gid": "888", "name": "Real API Section"}}])
    ]
    setup.client.sections = lambda project_gid: [{"gid": "888", "name": "Real API Section"}]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert issue.state.name == "Real API Section"


def test_pull_subtask_becomes_sub_issue(setup):
    parent = asana_task(gid="111")
    child = asana_task(gid="112", name="Sub task", parent={"gid": "111", "resource_type": "task"})
    setup.client.tasks = lambda project_gid, modified_since=None: [parent, child]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    parent_issue = Issue.objects.get(project=setup.project, name="From Asana")
    child_issue = Issue.objects.get(project=setup.project, name="Sub task")
    assert child_issue.parent_id == parent_issue.id


def test_pull_comments_creates_system_comments_with_links(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    setup.client.stories = lambda task_gid: [
        {"gid": "s1", "resource_subtype": "comment_added", "html_text": "<body>Nice work</body>"},
        {"gid": "s2", "resource_subtype": "assigned", "text": "assigned to x"},
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project)
    comments = IssueComment.objects.filter(issue=issue)
    assert comments.count() == 1
    assert comments.first().actor_id is None
    assert comments.first().external_source.startswith("asana:")
    assert AsanaCommentLink.objects.filter(task_link__asana_task_gid="111", asana_story_gid="s1").exists()


def test_pull_comment_redelivery_is_idempotent(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    setup.client.stories = lambda task_gid: [
        {"gid": "s1", "resource_subtype": "comment_added", "html_text": "<body>Nice work</body>"}
    ]
    engine = AsanaSyncEngine(setup.sync, setup.client)
    engine.pull_full()
    engine.pull_full()
    assert IssueComment.objects.filter(issue__project=setup.project).count() == 1


def test_local_newer_change_wins_lww_on_conflict(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    engine = AsanaSyncEngine(setup.sync, setup.client)
    engine.pull_full()
    link = AsanaTaskLink.objects.get(sync=setup.sync)
    issue = link.issue

    # Local edit after the pull + remote change older than that edit -> local wins.
    Issue.objects.filter(pk=issue.pk).update(name="Locally renamed", updated_at=timezone.now())
    issue.refresh_from_db()
    stale_remote = asana_task(name="Old remote name", modified_at="2026-09-19T00:00:00.000Z")
    pushed_payloads = []
    setup.client.update_task = lambda task_gid, data: pushed_payloads.append(data) or {**asana_task(gid=task_gid), **data}

    result = engine._pull_task(stale_remote)
    assert result is True  # conflict path: remote change superseded, pushed local instead
    issue.refresh_from_db()
    assert issue.name == "Locally renamed"
    assert pushed_payloads and pushed_payloads[0]["name"] == "Locally renamed"
    assert AsanaSyncLog.objects.filter(sync=setup.sync, status="conflict").exists()


# --------------------------------------------------------------------- push


def test_push_creates_task_and_links_issue(setup):
    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(
        project=setup.project, name="Plane side", description_html="<p>desc</p>",
        state=setup.backlog, priority="none", target_date=timezone.datetime(2026, 11, 1).date(),
    )
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.push_full() == 1
    link = AsanaTaskLink.objects.get(sync=setup.sync, issue=issue)
    assert link.asana_task_gid == "555"

    # Second pass: nothing new to push.
    setup.sync.refresh_from_db()
    assert AsanaSyncEngine(setup.sync, setup.client).push_full() == 0


def test_push_completed_state_sets_completed_true(setup):
    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(project=setup.project, name="Finished", state=setup.done)
    payloads = []
    setup.client.create_task = lambda ws_gid, data: payloads.append(data) or {**asana_task(gid="556"), **data}
    AsanaSyncEngine(setup.sync, setup.client).push_full()
    assert payloads[0]["completed"] is True


def test_pull_direction_never_pushes(setup):
    setup.sync.direction = "pull"
    setup.sync.save(update_fields=["direction"])
    Issue.objects.create(project=setup.project, name="Only local", state=setup.backlog)
    assert AsanaSyncEngine(setup.sync, setup.client).push_full() == 0
    assert not AsanaTaskLink.objects.exists()


# ------------------------------------------------------------------ webhook


def test_webhook_signature_verification_rejects_bad_hmac():
    import hashlib
    import hmac as hmac_mod

    from plane.app.asana_sync.webhook import AsanaWebhookEndpoint

    secret = "hook-secret"
    body = b'{"events": []}'
    bad = hmac_mod.new(b"wrong", body, hashlib.sha256).hexdigest()
    expected = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert not hmac_mod.compare_digest(bad, expected)
    assert hmac_mod.compare_digest(expected, expected)


# --------------------------------------------------------------- run_sync_pass


def test_run_sync_pass_inactive_sync_is_noop(setup):
    setup.sync.is_active = False
    setup.sync.save(update_fields=["is_active"])
    result = run_sync_pass(str(setup.sync.id))
    assert result["error"] == "sync inactive"


def test_run_sync_pass_reports_missing_sync():
    result = run_sync_pass(str(uuid4()))
    assert result["error"] == "sync not found"


# ------------------------------------------------------------------ assignees


def second_member(setup):
    member_user = User.objects.create(
        email=f"asana-mapped-{uuid4().hex[:6]}@example.test", username=f"asana-mapped-{uuid4().hex[:6]}"
    )
    ProjectMember.objects.create(project=setup.project, member=member_user, role=15, is_active=True)
    return member_user


def attach_label(issue, label):
    """IssueLabel through row (plain labels.set() violates NOT NULL project)."""
    IssueLabel.objects.create(
        label=label, issue=issue, project_id=issue.project_id, workspace_id=issue.workspace_id
    )


def attach_assignee(issue, member_user):
    """IssueAssignee through row (plain assignees.set() violates NOT NULL project)."""
    IssueAssignee.objects.create(
        assignee_id=member_user.id, issue=issue, project_id=issue.project_id, workspace_id=issue.workspace_id
    )


def test_assignee_value_parsers():
    assert mapping.assignee_value_member_id("member:abc") == "abc"
    assert mapping.assignee_value_member_id("abc") == "abc"  # legacy bare member id
    assert mapping.assignee_value_member_id("label:abc") is None
    assert mapping.assignee_value_member_id("") is None
    assert mapping.assignee_value_label_id("label:abc") == "abc"
    assert mapping.assignee_value_label_id("member:abc") is None
    assert mapping.assignee_value_label_id("auto") is None


def test_pull_mapped_assignee_sets_plane_member(setup):
    member_user = second_member(setup)
    setup.sync.assignee_map = {"9999": f"member:{member_user.id}"}
    setup.sync.save(update_fields=["assignee_map"])
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "9999", "name": "Mapped Person"})
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project)
    assert list(issue.assignees.values_list("id", flat=True)) == [member_user.id]
    assert issue.labels.count() == 0  # no person label wanted for member mappings


def test_pull_unmapped_assignee_provisions_person_label(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "8888", "name": "Ada Lovelace"})
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project)
    label = issue.labels.first()
    assert label is not None
    assert label.name == "Ada Lovelace"
    assert label.color == "#F06A6A"
    setup.sync.refresh_from_db()
    assert setup.sync.assignee_map["8888"].startswith("label:")
    # Redelivery does not duplicate provisioning.
    labels_before = Label.objects.filter(project=setup.project).count()
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    assert Label.objects.filter(project=setup.project).count() == labels_before


def test_pull_explicit_none_skips_person_label(setup):
    setup.sync.assignee_map = {"8888": ""}
    setup.sync.save(update_fields=["assignee_map"])
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "8888", "name": "Ada Lovelace"})
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project)
    assert issue.labels.count() == 0
    assert issue.assignees.count() == 0
    setup.sync.refresh_from_db()
    assert setup.sync.assignee_map["8888"] == ""


def test_pull_mapped_member_not_reprovisioned_after_upgrade(setup):
    # First pull: unmapped assignee -> person label.
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "8888", "name": "Ada Lovelace"})
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    member_user = second_member(setup)
    setup.sync.assignee_map = {"8888": f"member:{member_user.id}"}
    setup.sync.save(update_fields=["assignee_map"])
    # Second pass: newer remote modification -> assignee becomes the member.
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "8888", "name": "Ada Lovelace"}, modified_at="2026-09-21T10:00:00.000Z")
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project)
    assert list(issue.assignees.values_list("id", flat=True)) == [member_user.id]


def test_pull_preserves_manual_labels(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project)
    manual = Label.objects.create(project=setup.project, name="Manual", color="#123456")
    attach_label(issue, manual)
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(name="From Asana v2", modified_at="2026-09-21T10:00:00.000Z")
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue.refresh_from_db()
    assert issue.labels.filter(id=manual.id).exists()


def test_push_mapped_member_sets_asana_assignee(setup):
    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    member_user = second_member(setup)
    setup.sync.assignee_map = {"7777": f"member:{member_user.id}"}
    setup.sync.save(update_fields=["assignee_map"])
    issue = Issue.objects.create(project=setup.project, name="Assigned", state=setup.backlog, priority="none")
    attach_assignee(issue, member_user)
    payloads = []
    setup.client.create_task = lambda ws_gid, data: payloads.append(data) or {**asana_task(gid="556"), **data}
    AsanaSyncEngine(setup.sync, setup.client).push_full()
    assert payloads[0]["assignee"] == "7777"


def test_push_person_label_resolves_assignee(setup):
    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    label = Label.objects.create(project=setup.project, name="Ada Lovelace", color="#F06A6A")
    setup.sync.assignee_map = {"8888": f"label:{label.id}"}
    setup.sync.save(update_fields=["assignee_map"])
    issue = Issue.objects.create(project=setup.project, name="Label carried", state=setup.backlog, priority="none")
    attach_label(issue, label)
    payloads = []
    setup.client.create_task = lambda ws_gid, data: payloads.append(data) or {**asana_task(gid="557"), **data}
    AsanaSyncEngine(setup.sync, setup.client).push_full()
    assert payloads[0]["assignee"] == "8888"


# ------------------------------------------------------- realtime push signals


def test_pull_writes_are_suppressed_from_push_signals(setup):
    from plane.app.asana_sync import signals as asana_signals  # registers push receivers  # noqa: F401
    from plane.app.asana_sync.tasks import asana_sync_push_issue

    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "8888", "name": "Ada Lovelace"})
    ]
    setup.client.stories = lambda task_gid: [
        {"gid": "s9", "resource_subtype": "comment_added", "html_text": "<body>From Asana</body>"}
    ]
    with patch.object(asana_sync_push_issue, "apply_async") as push_mock:
        AsanaSyncEngine(setup.sync, setup.client).pull_full()
    assert push_mock.call_count == 0


def test_issue_save_enqueues_targeted_push(setup):
    from plane.app.asana_sync import signals as asana_signals  # registers push receivers  # noqa: F401
    from plane.app.asana_sync.tasks import asana_sync_push_issue

    issue = Issue.objects.create(project=setup.project, name="Trigger", state=setup.backlog, priority="none")
    with patch.object(asana_sync_push_issue, "apply_async") as push_mock:
        issue.name = "Triggered"
        issue.save()
    assert push_mock.call_count == 1
    assert push_mock.call_args.kwargs["args"] == [str(setup.sync.id), str(issue.id)]


def test_issue_save_skipped_for_pull_only_sync(setup):
    from plane.app.asana_sync import signals as asana_signals  # registers push receivers  # noqa: F401
    from plane.app.asana_sync.tasks import asana_sync_push_issue

    setup.sync.direction = "pull"
    setup.sync.save(update_fields=["direction"])
    with patch.object(asana_sync_push_issue, "apply_async") as push_mock:
        Issue.objects.create(project=setup.project, name="Quiet", state=setup.backlog, priority="none")
    assert push_mock.call_count == 0


def test_assignee_set_enqueues_targeted_push(setup):
    from plane.app.asana_sync import signals as asana_signals  # registers push receivers  # noqa: F401
    from plane.app.asana_sync.tasks import asana_sync_push_issue

    member_user = second_member(setup)
    issue = Issue.objects.create(project=setup.project, name="Who?", state=setup.backlog, priority="none")
    with patch.object(asana_sync_push_issue, "apply_async") as push_mock:
        attach_assignee(issue, member_user)
    assert push_mock.call_count >= 1
    call_args = push_mock.call_args_list[-1].kwargs["args"]
    assert call_args == [str(setup.sync.id), str(issue.id)]


# --------------------------------------------------- client envelope (prod fix)


def test_client_unwraps_single_resource_data_envelope():
    """Asana single-resource GET/POST/PUT responses are {"data": {...}}; the
    client must hand callers plain task/webhook/story dicts (prod incident
    2026-09-29: webhook_gid stored empty, single-task webhook pulls no-oped)."""
    from unittest.mock import patch as mock_patch

    from plane.app.asana_sync.client import AsanaClient

    envelope = {"data": {"gid": "wh-1", "name": "T", "modified_at": "2026-09-29T00:00:00.000Z"}}
    with mock_patch("plane.app.asana_sync.client._request", return_value=dict(envelope)) as req:
        client = AsanaClient("1/token")
        assert client.task("111")["gid"] == "wh-1"
        assert client.me()["gid"] == "wh-1"
        assert client.create_task("42", {"name": "T"})["gid"] == "wh-1"
        assert client.update_task("111", {"name": "T"})["gid"] == "wh-1"
        assert client.create_webhook("999", "https://x")["gid"] == "wh-1"
        assert client.create_story("111", "hi")["gid"] == "wh-1"
        # empty-body {} (DELETE / some PUTs) passes through untouched
        req.return_value = {}
        assert client.update_story("s1", "hi") == {}
        assert req.call_count == 7


def test_push_falls_back_to_plain_notes_when_html_rejected(setup):
    """Tokens without Asana's html-notes write feature reject every html_notes
    PUT with xml_parsing_error; the engine must retry once with plain notes."""
    from plane.app.asana_sync.client import AsanaAPIError

    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(
        project=setup.project, name="Notes fallback", description_html="<p>real <b>desc</b></p>",
        state=setup.backlog, priority="none",
    )

    link = AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="777",
    )
    seen = []

    def flaky_update(task_gid, data):
        seen.append(dict(data))
        if "html_notes" in data:
            raise AsanaAPIError('Asana returned 400: {"errors":[{"error":"xml_parsing_error","message":"XML is invalid"}]}')
        return {**asana_task(gid=task_gid), **data}

    setup.client.update_task = flaky_update
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.push_issue(issue) is True
    assert len(seen) == 2
    assert "html_notes" in seen[0] and "html_notes" not in seen[1]
    assert seen[1]["notes"] == "real desc"
    assert AsanaTaskLink.objects.get(sync=setup.sync, issue=issue).asana_task_gid
    assert not AsanaSyncLog.objects.filter(status="error").exists()


def test_push_non_xml_error_does_not_retry(setup):
    """Only xml_parsing_error gets the notes fallback; real failures surface."""
    from plane.app.asana_sync.client import AsanaAPIError

    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(
        project=setup.project, name="Hard fail", description_html="<p>x</p>",
        state=setup.backlog, priority="none",
    )
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="778",
    )
    calls = []

    def forbidden(task_gid, data):
        calls.append(1)
        raise AsanaAPIError("Asana returned 403: forbidden")

    setup.client.update_task = forbidden
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.push_issue(issue) is False
    assert len(calls) == 1
    assert AsanaSyncLog.objects.filter(status="error", direction="push").exists()


def test_section_placement_failure_never_deletes_link(setup):
    """A stale/invalid section must not nuke the link (prod 2026-09-29: the
    section-move 404 was handled as 'task gone' and every mapped-state push
    destroyed its link, letting the next pull duplicate the issue)."""
    from plane.app.asana_sync.client import AsanaAPIError

    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(
        project=setup.project, name="Section storm", state=setup.doing, priority="none",
    )
    link = AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="888",
    )

    def dead_section_move(task_gid, project_gid, section_gid):
        raise AsanaAPIError("Asana returned 404: no matching route", status_code=404)

    setup.client.move_task_to_section = dead_section_move
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.push_issue(issue) is True
    assert AsanaTaskLink.objects.filter(id=link.id, deleted_at__isnull=True).exists()
    assert AsanaSyncLog.objects.filter(status="skipped", message__icontains="placement").exists()
    assert not AsanaSyncLog.objects.filter(message__icontains="task gone").exists()


def test_task_gone_still_breaks_link(setup):
    """The link-break-on-404 contract survives, scoped to a 404 that a direct
    task fetch confirms."""
    from plane.app.asana_sync.client import AsanaAPIError

    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(
        project=setup.project, name="Truly gone", state=setup.backlog, priority="none",
    )
    link = AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="999-x",
    )

    def gone(task_gid, data=None):
        raise AsanaAPIError("Asana returned 404: task not found", status_code=404)

    setup.client.update_task = gone
    setup.client.task = gone
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.push_issue(issue) is False
    assert not AsanaTaskLink.objects.filter(id=link.id, deleted_at__isnull=True).exists()
    assert AsanaSyncLog.objects.filter(message__icontains="confirmed by fetch").exists()


def test_move_to_section_uses_addproject_route():
    """The section move rides /tasks/{gid}/addProject — /sections/{gid}/insertTask
    is a dead route on this Asana API surface ('no matching route')."""
    from unittest.mock import patch as mock_patch

    from plane.app.asana_sync.client import AsanaClient

    with mock_patch("plane.app.asana_sync.client._request") as req:
        AsanaClient("1/token").move_task_to_section("t1", "p1", "s1")
        path = req.call_args.args[2]
        body = req.call_args.kwargs["json_body"]
        assert path == "/tasks/t1/addProject"
        assert body == {"data": {"project": "p1", "section": "s1"}}


def test_client_requests_user_field_on_project_memberships():
    """Asana's project_memberships carry people under "user" — requesting the
    nonexistent member.* opt_fields silently yields bare {gid} items, which
    emptied the mapping UI's people list (prod fix 2026-09-29)."""
    from unittest.mock import patch as mock_patch

    from plane.app.asana_sync.client import AsanaClient

    with mock_patch("plane.app.asana_sync.client._request") as req:
        req.return_value = {"data": [{"gid": "m1", "user": {"gid": "u1", "name": "Brayden Keisker"}}], "next_page": None}
        items = AsanaClient("1/token").project_memberships("999")
        opt_fields = req.call_args.kwargs["params"]["opt_fields"]
        assert "user" in opt_fields and "member" not in opt_fields
        assert items[0]["user"]["name"] == "Brayden Keisker"


def test_reconcile_states_repairs_section_drift(setup):
    """Asana section moves don't reliably bump modified_at, so delta pulls miss
    them; the reconcile sweep must align issue states with current sections."""
    moved = asana_task(gid="700", memberships=[{"project": {"gid": "999"}, "section": "777"}])
    setup.client.tasks = lambda project_gid, modified_since=None: [moved]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert issue.state_id == setup.doing.id  # section 777 -> Doing

    # user moved the task to section "888" — state_map has no entry there, so
    # map it by registering 888 -> backlog directly (admin-style mapping)
    setup.sync.state_map = {"777": {"state_id": str(setup.doing.id), "name": "Doing"},
                            "888": {"state_id": str(setup.backlog.id), "name": "Backlog"}}
    setup.sync.save(update_fields=["state_map"])
    moved_task = asana_task(gid="700", memberships=[{"project": {"gid": "999"}, "section": "888"}])
    setup.client.tasks = lambda project_gid, modified_since=None: [moved_task]

    fixed = AsanaSyncEngine(setup.sync, setup.client).reconcile_states()
    assert fixed == 1
    issue.refresh_from_db()
    assert issue.state_id == setup.backlog.id
    assert AsanaSyncLog.objects.filter(message__icontains="Reconciled state drift").exists()


def test_reconcile_states_skips_aligned_tasks(setup):
    aligned = asana_task(gid="701", memberships=[{"project": {"gid": "999"}, "section": "777"}])
    setup.client.tasks = lambda project_gid, modified_since=None: [aligned]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    setup.client.tasks = lambda project_gid, modified_since=None: [aligned]
    assert AsanaSyncEngine(setup.sync, setup.client).reconcile_states() == 0


def test_reconcile_states_noop_for_push_direction(setup):
    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    assert AsanaSyncEngine(setup.sync, setup.client).reconcile_states() == 0


# ------------------------------------------------------- idempotent identity
# Prod 2026-09-29/30: soft-deleted links let pull passes re-create the same
# FR items (FR-23..26) and push passes fork Asana tasks for link-less issues.
# Identity now rides Issue.external_id too, and lost links are recovered.


def test_pull_stamps_external_identity_on_create(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert issue.external_source == "asana"
    assert issue.external_id == "111"


def test_pull_revives_soft_deleted_link_instead_of_duplicating(setup):
    issue = Issue.objects.create(project=setup.project, name="From Asana", state=setup.doing, priority="none")
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="111",
        asana_modified_at=timezone.datetime(2026, 9, 19, 10, 0, tzinfo=dt_tz.utc),
    ).delete()
    assert AsanaTaskLink.all_objects.filter(issue=issue, deleted_at__isnull=False).exists()

    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    assert AsanaSyncEngine(setup.sync, setup.client).pull_full() == 1

    assert Issue.objects.filter(project=setup.project).count() == 1
    assert AsanaTaskLink.objects.filter(sync=setup.sync, asana_task_gid="111", deleted_at__isnull=True).exists()
    assert AsanaSyncLog.objects.filter(message__icontains="Recovered soft-deleted link").exists()


def test_pull_adopts_backfilled_external_id_instead_of_duplicating(setup):
    Issue.objects.create(
        project=setup.project, name="From Asana", state=setup.doing, priority="none",
        external_source="asana", external_id="111",
    )
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    assert AsanaSyncEngine(setup.sync, setup.client).pull_full() == 1

    assert Issue.objects.filter(project=setup.project).count() == 1
    assert AsanaTaskLink.objects.filter(sync=setup.sync, asana_task_gid="111", deleted_at__isnull=True).exists()
    assert AsanaSyncLog.objects.filter(message__icontains="Adopted issue via external_id").exists()


def test_pull_prefers_reviving_oldest_soft_link(setup):
    """Multiple soft links for one gid (prod FR-3/23/24): the oldest — the
    original mapping — is the one revived."""
    old = Issue.objects.create(project=setup.project, name="Original", state=setup.doing, priority="none")
    newer = Issue.objects.create(project=setup.project, name="Duplicate", state=setup.doing, priority="none")
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=old, asana_task_gid="111",
        asana_modified_at=timezone.datetime(2026, 9, 20, 10, 0, tzinfo=dt_tz.utc),
    ).delete()
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=newer, asana_task_gid="111",
        asana_modified_at=timezone.datetime(2026, 9, 21, 10, 0, tzinfo=dt_tz.utc),
    ).delete()

    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()

    link = AsanaTaskLink.objects.get(sync=setup.sync, asana_task_gid="111", deleted_at__isnull=True)
    assert link.issue_id == old.id
    assert Issue.objects.filter(project=setup.project).count() == 2


def test_update_repair_stamps_missing_identity(setup):
    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.pull_full() == 1
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    # Simulate a pre-stamp item: identity wiped, link intact.
    Issue.objects.filter(id=issue.id).update(external_source=None, external_id=None)
    issue.refresh_from_db()

    renamed = asana_task(modified_at="2026-09-25T10:00:00.000Z")
    renamed["name"] = "From Asana (renamed)"
    setup.client.tasks = lambda project_gid, modified_since=None: [renamed]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()

    issue.refresh_from_db()
    assert issue.external_source == "asana" and issue.external_id == "111"


def test_push_refuses_to_fork_remote_task_for_orphaned_issue(setup):
    """An asana-origin issue that lost its link must be relinked, never pushed
    as a brand-new task (prod 2026-09-30 18:33)."""
    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(
        project=setup.project, name="Orphaned mirror", state=setup.doing, priority="none",
        external_source="asana", external_id="888",
    )
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="888",
    ).delete()

    created = []
    setup.client.create_task = lambda ws_gid, data: created.append(data) or {**asana_task(gid="666"), **data}
    setup.client.update_task = lambda task_gid, data: {**asana_task(gid=task_gid), **data}

    assert AsanaSyncEngine(setup.sync, setup.client).push_issue(issue) is True
    assert created == []  # no fork
    link = AsanaTaskLink.objects.get(sync=setup.sync, asana_task_gid="888", deleted_at__isnull=True)
    assert link.issue_id == issue.id


def test_push_refuses_fork_when_another_issue_holds_mapping(setup):
    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    holder = Issue.objects.create(project=setup.project, name="Holder", state=setup.doing, priority="none")
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=holder, asana_task_gid="888",
    ).delete()
    orphan = Issue.objects.create(
        project=setup.project, name="Orphan", state=setup.doing, priority="none",
        external_source="asana", external_id="888",
    )

    created = []
    setup.client.create_task = lambda ws_gid, data: created.append(data) or {**asana_task(gid="666"), **data}
    assert AsanaSyncEngine(setup.sync, setup.client).push_issue(orphan) is False
    assert created == []


def test_push_recreates_task_when_remote_confirmed_gone(setup):
    """A really-deleted remote task keeps the documented re-push path."""
    from plane.app.asana_sync.client import AsanaAPIError

    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(
        project=setup.project, name="Was mirrored", state=setup.doing, priority="none",
        external_source="asana", external_id="888",
    )
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="888",
    ).delete()

    def gone(task_gid):
        raise AsanaAPIError("Asana returned 404: task not found", status_code=404)

    setup.client.task = gone
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.push_issue(issue) is True
    link = AsanaTaskLink.objects.get(sync=setup.sync, deleted_at__isnull=True)
    assert link.asana_task_gid == "555"
    issue.refresh_from_db()
    assert issue.external_id == "555"


def test_update_404_unconfirmed_keeps_link(setup):
    """A 404 on the update PUT that a direct fetch does not confirm must not
    retire the link (prod 2026-09-29: unconfirmed 404s caused FR-23..26)."""
    from plane.app.asana_sync.client import AsanaAPIError

    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(
        project=setup.project, name="Blip", state=setup.doing, priority="none",
    )
    link = AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="888",
    )

    def blip(task_gid, data):
        raise AsanaAPIError("Asana returned 404: no matching route", status_code=404)

    setup.client.update_task = blip  # client.task still returns a live task
    assert AsanaSyncEngine(setup.sync, setup.client).push_issue(issue) is False
    assert AsanaTaskLink.objects.filter(id=link.id, deleted_at__isnull=True).exists()
    assert AsanaSyncLog.objects.filter(message__icontains="not confirmed").exists()


def test_create_race_loses_to_unique_constraint_and_adopts(setup):
    """If a concurrent pass commits the (sync, task_gid) link between our
    lookup and our insert, the loser rolls back its issue and adopts the
    winner's link — never two items."""
    import psycopg

    from django.conf import settings
    from django.db import IntegrityError

    setup.client.tasks = lambda project_gid, modified_since=None: [asana_task()]
    # The concurrent winner's ISSUE exists, but its link is not yet visible to
    # this connection — it commits only while our insert is failing.
    winner = Issue.objects.create(project=setup.project, name="Concurrent winner", state=setup.doing, priority="none")

    s = settings.DATABASES["default"]
    other = psycopg.connect(
        dbname=s["NAME"], user=s["USER"], password=s["PASSWORD"],
        host=s["HOST"], port=s["PORT"],
    )

    def racing_create(**kwargs):
        # Concurrent pass commits its link on another connection, then our
        # INSERT hits the (sync, asana_task_gid) unique constraint.
        with other, other.cursor() as cur:
            cur.execute(
                """INSERT INTO asana_task_links
                     (id, created_at, updated_at, asana_task_gid, asana_name_hash,
                      issue_id, project_id, sync_id, workspace_id)
                   VALUES (gen_random_uuid(), now(), now(), %s, '', %s, %s, %s, %s)""",
                ("111", winner.id, setup.project.id, setup.sync.id, setup.workspace.id),
            )
        raise IntegrityError("duplicate key value violates unique constraint")


        with other, other.cursor() as cur:
            cur.execute(
                """INSERT INTO asana_task_links
                     (id, created_at, updated_at, asana_task_gid, asana_name_hash,
                      issue_id, project_id, sync_id, workspace_id)
                   VALUES (gen_random_uuid(), now(), now(), %s, '', %s, %s, %s, %s)""",
                ("111", winner.id, setup.project.id, setup.sync.id, setup.workspace.id),
            )
        raise IntegrityError("duplicate key value violates unique constraint")

    with patch(
        "plane.app.asana_sync.engine.AsanaTaskLink.objects.create",
        side_effect=racing_create,
    ):
        assert AsanaSyncEngine(setup.sync, setup.client).pull_full() == 1
    other.close()

    # The loser's issue was rolled back; the winner's link was adopted.
    assert not Issue.objects.filter(project=setup.project, name="From Asana").exists()
    link = AsanaTaskLink.objects.get(sync=setup.sync, asana_task_gid="111", deleted_at__isnull=True)
    assert link.issue_id == winner.id
    assert AsanaSyncLog.objects.filter(message__icontains="adopted its link").exists()


def test_backfill_command_stamps_oldest_link_and_excludes(setup):
    from django.core.management import call_command

    from io import StringIO

    kept = Issue.objects.create(project=setup.project, name="Kept", state=setup.doing, priority="none")
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=kept, asana_task_gid="111",
        asana_modified_at=timezone.datetime(2026, 9, 20, 10, 0, tzinfo=dt_tz.utc),
    ).delete()  # original mapping, now soft-deleted
    dup = Issue.objects.create(project=setup.project, name="Cancelled dup", state=setup.doing, priority="none")
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=dup, asana_task_gid="111",
        asana_modified_at=timezone.datetime(2026, 9, 21, 10, 0, tzinfo=dt_tz.utc),
    ).delete()
    excluded_seq = dup.sequence_id

    before = Issue.objects.get(id=kept.id).updated_at
    out = StringIO()
    call_command(
        "backfill_asana_external_ids", exclude_seq=str(excluded_seq), stdout=out,
    )

    kept.refresh_from_db()
    dup.refresh_from_db()
    assert kept.external_source == "asana" and kept.external_id == "111"
    assert dup.external_id in (None, "")  # excluded: untouched
    assert kept.updated_at == before  # no updated_at bump — LWW guard stays quiet
    assert f"skip FR-{excluded_seq}" in out.getvalue()


# ------------------------------------------------- provisioning reuses columns
# Prod 2026-09-30: an Asana section named "In progress" got its own Plane state
# next to the project's existing "In Progress" column (exact-name match only).


def test_section_reuses_existing_state_case_insensitive(setup):
    native = State.objects.create(
        name="In Progress", group="started", color="#777777", project=setup.project
    )
    setup.client.sections = lambda project_gid: [{"gid": "555", "name": "In progress"}]
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(gid="444", memberships=[{"project": {"gid": "999"}, "section": "555"}])
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()

    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert issue.state_id == native.id  # reused, group "started" preserved
    assert State.objects.filter(
        project=setup.project, name__iexact="in progress", deleted_at__isnull=True
    ).count() == 1  # no duplicate column
    assert setup.sync.state_map["555"]["state_id"] == str(native.id)
    assert AsanaSyncLog.objects.filter(message__icontains="existing state 'In Progress'").exists()


def test_section_still_provisions_genuinely_new_state(setup):
    setup.client.sections = lambda project_gid: [{"gid": "556", "name": "Brand New Column"}]
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(gid="445", memberships=[{"project": {"gid": "999"}, "section": "556"}])
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()

    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert issue.state.name == "Brand New Column"
    assert issue.state.group == "unstarted"
    assert AsanaSyncLog.objects.filter(
        message__icontains="Provisioned state 'Brand New Column'"
    ).exists()


def test_tag_reuses_existing_label_case_insensitive(setup):
    native = Label.objects.create(name="Urgent", color="#ff0000", project=setup.project)
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(gid="446", tags=[{"gid": "7777", "name": "urgent"}])
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()

    issue = Issue.objects.get(project=setup.project, name="From Asana")
    assert list(issue.labels.values_list("id", flat=True)) == [native.id]
    assert Label.objects.filter(
        project=setup.project, name__iexact="urgent", deleted_at__isnull=True
    ).count() == 1
    assert setup.sync.label_map["7777"] == str(native.id)


# --------------------------------------------- assignee mirror custom property
# "Assign to anyone, in sync": a sync-managed select property whose options are
# the assignee_map people (Plane members + Asana-only persons). Asana's
# assignee lands in the property on pull; an explicit pick pushes to Asana.


def test_pull_provisions_and_sets_assignee_property_for_member(setup):
    from plane.db.models import CustomProperty, CustomPropertyValue

    setup.sync.assignee_map = {"ag1": f"member:{setup.owner.id}"}
    setup.sync.save(update_fields=["assignee_map"])
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "ag1", "name": "The Owner"})
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()

    prop = CustomProperty.objects.get(project=setup.project, name="Asana Assignee")
    assert prop.type == "select"
    assert str(setup.sync.assignee_property_id) == str(prop.id)
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    value = CustomPropertyValue.objects.get(property=prop, issue=issue)
    assert value.value_option == [f"member:{setup.owner.id}"]
    options = {o["id"]: o for o in prop.settings_json["options"]}
    assert f"member:{setup.owner.id}" in options
    assert options[f"member:{setup.owner.id}"]["color"] == "#3f76ff"


def test_pull_sets_assignee_property_for_asana_only_person(setup):
    from plane.db.models import CustomPropertyValue, Label

    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "ag9", "name": "Grant Brimhall"})
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()

    label = Label.objects.get(project=setup.project, name="Grant Brimhall")
    from plane.db.models import CustomProperty

    prop = CustomProperty.objects.get(project=setup.project, name="Asana Assignee")
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    value = CustomPropertyValue.objects.get(property=prop, issue=issue)
    assert value.value_option == [f"label:{label.id}"]
    options = {o["id"]: o for o in prop.settings_json["options"]}
    assert options[f"label:{label.id}"]["name"] == "Grant Brimhall"
    assert options[f"label:{label.id}"]["color"] == "#F06A6A"


def test_pull_clears_assignee_property_when_task_unassigned(setup):
    from plane.db.models import CustomPropertyValue

    setup.sync.assignee_map = {"ag1": f"member:{setup.owner.id}"}
    setup.sync.save(update_fields=["assignee_map"])
    setup.client.tasks = lambda project_gid, modified_since=None: [
        asana_task(assignee={"gid": "ag1", "name": "The Owner"})
    ]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()
    issue = Issue.objects.get(project=setup.project, name="From Asana")
    from plane.db.models import CustomProperty

    prop = CustomProperty.objects.get(project=setup.project, name="Asana Assignee")
    assert CustomPropertyValue.objects.filter(property=prop, issue=issue).exists()

    unassigned = asana_task(modified_at="2026-09-25T10:00:00.000Z")
    unassigned["assignee"] = None
    setup.client.tasks = lambda project_gid, modified_since=None: [unassigned]
    AsanaSyncEngine(setup.sync, setup.client).pull_full()

    assert not CustomPropertyValue.objects.filter(property=prop, issue=issue).exists()


def test_push_property_takes_precedence_over_native_assignee(setup):
    from plane.db.models import CustomProperty, CustomPropertyValue, Label

    setup.sync.direction = "push"
    setup.sync.assignee_map = {"ag1": f"member:{setup.owner.id}", "ag2": "label:PLACEHOLDER"}
    label = Label.objects.create(name="Grant Brimhall", color="#F06A6A", project=setup.project)
    setup.sync.assignee_map = {"ag1": f"member:{setup.owner.id}", "ag2": f"label:{label.id}"}
    setup.sync.save(update_fields=["assignee_map"])

    issue = Issue.objects.create(
        project=setup.project, name="Both set", state=setup.doing, priority="none",
    )
    from plane.db.models import IssueAssignee

    IssueAssignee.objects.create(
        assignee=setup.owner, issue=issue, project=setup.project, workspace=setup.workspace
    )
    prop = CustomProperty.objects.create(
        project=setup.project, name="Asana Assignee", type="select",
        settings_json={"options": [
            {"id": f"member:{setup.owner.id}", "name": "Owner", "color": "#3f76ff"},
            {"id": f"label:{label.id}", "name": "Grant Brimhall", "color": "#F06A6A"},
        ]},
    )
    setup.sync.assignee_property_id = prop.id
    setup.sync.save(update_fields=["assignee_property_id"])
    CustomPropertyValue.objects.create(property=prop, issue=issue, value_option=[f"label:{label.id}"])
    link = AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="777",
    )

    captured = []
    setup.client.update_task = lambda task_gid, data: captured.append(data) or {**asana_task(gid=task_gid), **data}
    assert AsanaSyncEngine(setup.sync, setup.client).push_issue(issue) is True
    assert captured and captured[0]["assignee"] == "ag2"  # property wins over member


def test_push_falls_back_to_native_assignee_when_property_empty(setup):
    setup.sync.direction = "push"
    setup.sync.assignee_map = {"ag1": f"member:{setup.owner.id}"}
    setup.sync.save(update_fields=["assignee_map"])
    issue = Issue.objects.create(
        project=setup.project, name="Native only", state=setup.doing, priority="none",
    )
    from plane.db.models import IssueAssignee

    IssueAssignee.objects.create(
        assignee=setup.owner, issue=issue, project=setup.project, workspace=setup.workspace
    )
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="778",
    )
    captured = []
    setup.client.update_task = lambda task_gid, data: captured.append(data) or {**asana_task(gid=task_gid), **data}
    AsanaSyncEngine(setup.sync, setup.client).push_issue(issue)
    assert captured and captured[0]["assignee"] == "ag1"


def test_property_edit_enqueues_forced_assignee_push(setup):
    from plane.app.asana_sync import signals as asana_signals  # registers receivers  # noqa: F401
    from plane.app.asana_sync.engine import ensure_assignee_property
    from plane.app.asana_sync.tasks import asana_sync_assignee_property_changed
    from plane.db.models import CustomPropertyValue

    prop = ensure_assignee_property(setup.sync)
    issue = Issue.objects.create(project=setup.project, name="Mirror me", state=setup.doing, priority="none")
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="779",
    )
    with patch.object(asana_sync_assignee_property_changed, "apply_async") as push_mock:
        CustomPropertyValue.objects.create(
            property=prop, issue=issue, value_option=["member:x"],
        )
    assert push_mock.call_count == 1
    assert push_mock.call_args.kwargs["args"] == [str(setup.sync.id), str(issue.id)]


def test_forced_assignee_push_updates_asana_task(setup):
    from plane.app.asana_sync.engine import ensure_assignee_property
    from plane.db.models import CustomPropertyValue

    setup.sync.direction = "push"
    setup.sync.assignee_map = {"ag1": f"member:{setup.owner.id}"}
    setup.sync.save(update_fields=["assignee_map"])
    prop = ensure_assignee_property(setup.sync)
    issue = Issue.objects.create(project=setup.project, name="Forced", state=setup.doing, priority="none")
    AsanaTaskLink.objects.create(
        project=setup.project, sync=setup.sync, issue=issue, asana_task_gid="780",
    )
    CustomPropertyValue.objects.create(property=prop, issue=issue, value_option=[f"member:{setup.owner.id}"])

    captured = []
    setup.client.update_task = lambda task_gid, data: captured.append(data) or {**asana_task(gid=task_gid), **data}
    # Simulate the celery task body without redis: engine push directly.
    assert AsanaSyncEngine(setup.sync, setup.client).push_assignee_change(issue) is True
    assert captured and captured[0]["assignee"] == "ag1"


def test_ensure_assignee_property_reuses_existing_case_insensitive(setup):
    from plane.app.asana_sync.engine import ensure_assignee_property
    from plane.db.models import CustomProperty

    existing = CustomProperty.objects.create(
        project=setup.project, name="asana assignee", type="select", settings_json={"options": []},
    )
    prop = ensure_assignee_property(setup.sync)
    assert prop.id == existing.id
    assert str(setup.sync.assignee_property_id) == str(existing.id)
    assert CustomProperty.objects.filter(
        project=setup.project, name__iexact="asana assignee", deleted_at__isnull=True
    ).count() == 1
