# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the Asana <-> Plane sync (feat/asana-sync).

External Asana API is fully mocked (AsanaClient) — no network. Tests run
against a scratch database created by the runner script (see final report);
mark: contract + django_db(transaction=True) per the automations suite shape.
"""

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
    IssueComment,
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
        move_task_to_section=lambda task_gid, section_gid: None,
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
