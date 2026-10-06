"""Regressions for human state changes lost after rejected Asana pushes."""

from datetime import timedelta
from unittest.mock import patch
from xml.etree import ElementTree

import pytest
from django.utils import timezone

from plane.app.asana_sync import mapping
from plane.app.asana_sync.client import AsanaAPIError
from plane.app.asana_sync.engine import AsanaSyncEngine, run_sync_pass
from plane.db.models import AsanaSyncLog, AsanaTaskLink, Issue, IssueActivity
from plane.tests.contract.app.test_asana_sync import asana_task, isolate_bg, setup as sync_setup  # noqa: F401

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def setup(sync_setup):  # noqa: F811 - pytest injects the imported fixture
    return sync_setup


def linked_issue(setup):
    setup.sync.state_map["888"] = {"state_id": str(setup.backlog.id), "name": "Backlog"}
    setup.sync.save(update_fields=["state_map"])
    issue = Issue.objects.create(
        project=setup.project,
        name="Human state change",
        state=setup.backlog,
        description_html='<p class="editor">See <a class="link" target="_blank" '
        'rel="noopener" href="https://example.com/?a=1&amp;b=2">source</a></p>',
    )
    link = AsanaTaskLink.objects.create(
        project=setup.project,
        sync=setup.sync,
        issue=issue,
        asana_task_gid="111",
        plane_synced_at=issue.updated_at,
        asana_modified_at=timezone.now() - timedelta(days=1),
    )
    return issue, link


def test_outbound_html_strips_editor_attributes_and_keeps_link():
    notes = mapping.plane_html_to_asana_notes_html(
        '<p class="editor" data-id="1">See <a class="link" target="_blank" '
        'rel="noopener" href="https://example.com/?a=1&amp;b=2">source</a><br>more</p>'
    )
    root = ElementTree.fromstring(notes)
    assert root.find("p").attrib == {}
    assert root.find("p/a").attrib == {"href": "https://example.com/?a=1&b=2"}
    assert root.find("p/a").text == "source"


def test_outbound_html_preserves_asana_mentions():
    root = ElementTree.fromstring(
        mapping.plane_html_to_asana_notes_html(
            '<p><a data-asana-gid="123" data-asana-dynamic="false" class="editor">Person</a></p>'
        )
    )
    assert root.find("p/a").attrib == {"data-asana-gid": "123", "data-asana-dynamic": "false"}


def test_failed_push_cannot_reconcile_over_human_state(setup):
    issue, link = linked_issue(setup)
    issue.state = setup.doing
    issue.save(update_fields=["state", "updated_at"])
    setup.client.tasks = lambda *args, **kwargs: [asana_task(memberships=[{"section": "888"}])]

    def reject(*args):
        raise AsanaAPIError("Asana returned 400: html_anchor_unknown_attribute")

    setup.client.update_task = reject
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.push_issue(issue) is False
    assert engine.reconcile_states() == 0
    issue.refresh_from_db()
    link.refresh_from_db()
    assert issue.state_id == setup.doing.id
    assert link.plane_synced_at < issue.updated_at
    assert AsanaSyncLog.objects.filter(issue=issue, direction="pull", status="conflict").exists()


def test_full_pass_retries_pending_change_outside_global_cursor(setup):
    issue, link = linked_issue(setup)
    issue.state = setup.doing
    issue.save(update_fields=["state", "updated_at"])
    setup.sync.initial_sync_done = True
    setup.sync.last_synced_at = timezone.now()
    setup.sync.save(update_fields=["initial_sync_done", "last_synced_at"])
    seen = []
    setup.client.update_task = lambda gid, data: seen.append(gid) or asana_task(gid=gid)
    setup.client.tasks = lambda gid, modified_since=None: (
        [] if modified_since else [asana_task(memberships=[{"section": "888"}])]
    )
    with patch("plane.app.asana_sync.engine.AsanaClient", return_value=setup.client):
        result = run_sync_pass(setup.sync.id)
    assert result["pushed"] == 1
    assert seen == ["111"]
    issue.refresh_from_db()
    assert issue.state_id == setup.doing.id


def test_failed_section_move_preserves_pending_watermark(setup):
    issue, link = linked_issue(setup)
    watermark = link.plane_synced_at
    issue.state = setup.doing
    issue.save(update_fields=["state", "updated_at"])

    def reject(*args):
        raise AsanaAPIError("Asana returned 404: missing section", status_code=404)

    setup.client.move_task_to_section = reject
    assert AsanaSyncEngine(setup.sync, setup.client).push_issue(issue) is False
    link.refresh_from_db()
    assert link.deleted_at is None
    assert link.plane_synced_at == watermark


def test_push_watermark_does_not_swallow_concurrent_edit(setup):
    issue, link = linked_issue(setup)
    issue.state = setup.doing
    issue.save(update_fields=["state", "updated_at"])
    pushed_at = issue.updated_at

    def update(gid, data):
        concurrent = Issue.objects.get(id=issue.id)
        concurrent.name = "Edited while Asana request was in flight"
        concurrent.save(update_fields=["name", "updated_at"])
        return asana_task(gid=gid)

    setup.client.update_task = update
    assert AsanaSyncEngine(setup.sync, setup.client).push_issue(issue) is True
    link.refresh_from_db()
    issue.refresh_from_db()
    assert link.plane_synced_at == pushed_at
    assert link.plane_synced_at < issue.updated_at


def test_reconcile_aligned_remote_move_records_explicit_activity(setup):
    issue, link = linked_issue(setup)
    setup.client.tasks = lambda *args, **kwargs: [asana_task()]
    from plane.app.asana_sync.tasks import asana_sync_push_issue

    with patch.object(asana_sync_push_issue, "apply_async") as echo:
        assert AsanaSyncEngine(setup.sync, setup.client).reconcile_states() == 1
    assert not echo.called
    activity = IssueActivity.objects.get(issue=issue, field="state")
    assert activity.old_identifier == setup.backlog.id
    assert activity.new_identifier == setup.doing.id
    assert "Asana" in activity.comment


def test_pull_only_keeps_remote_state_authority(setup):
    issue, link = linked_issue(setup)
    issue.state = setup.done
    issue.save(update_fields=["state", "updated_at"])
    setup.sync.direction = "pull"
    setup.sync.save(update_fields=["direction"])
    setup.client.tasks = lambda *args, **kwargs: [asana_task()]
    assert AsanaSyncEngine(setup.sync, setup.client).reconcile_states() == 1
    issue.refresh_from_db()
    assert issue.state_id == setup.doing.id


def test_new_task_failed_placement_retries_without_duplicate(setup):
    setup.sync.direction = "push"
    setup.sync.save(update_fields=["direction"])
    issue = Issue.objects.create(project=setup.project, name="New pending task", state=setup.doing)
    created = []
    setup.client.create_task = lambda ws, data: created.append(data) or asana_task(gid="new-task")

    def reject(*args):
        raise AsanaAPIError("Asana returned 404: missing section", status_code=404)

    setup.client.move_task_to_section = reject
    engine = AsanaSyncEngine(setup.sync, setup.client)
    assert engine.push_issue(issue) is False
    link = AsanaTaskLink.objects.get(issue=issue, sync=setup.sync)
    assert link.plane_synced_at is None
    setup.client.move_task_to_section = lambda *args: None
    assert AsanaSyncEngine(setup.sync, setup.client).push_full() == 1
    assert len(created) == 1
    assert AsanaTaskLink.objects.filter(issue=issue, sync=setup.sync).count() == 1


def test_reconcile_rechecks_edit_made_during_remote_listing(setup):
    issue, link = linked_issue(setup)

    def listing(*args, **kwargs):
        issue.state = setup.done
        issue.save(update_fields=["state", "updated_at"])
        return [asana_task()]

    setup.client.tasks = listing
    assert AsanaSyncEngine(setup.sync, setup.client).reconcile_states() == 0
    issue.refresh_from_db()
    assert issue.state_id == setup.done.id


def test_full_pass_rejected_push_stays_pending_for_next_pass(setup):
    issue, link = linked_issue(setup)
    issue.state = setup.doing
    issue.save(update_fields=["state", "updated_at"])
    setup.sync.initial_sync_done = True
    setup.sync.last_synced_at = timezone.now()
    setup.sync.save(update_fields=["initial_sync_done", "last_synced_at"])
    setup.client.tasks = lambda gid, modified_since=None: (
        [] if modified_since else [asana_task(memberships=[{"section": "888"}])]
    )

    def forbidden(*args):
        raise AsanaAPIError("Asana returned 403: forbidden")

    setup.client.update_task = forbidden
    with patch("plane.app.asana_sync.engine.AsanaClient", return_value=setup.client):
        result = run_sync_pass(setup.sync.id)
    assert result["pushed"] == 0
    assert result["reconciled"] == 0
    issue.refresh_from_db()
    assert issue.state_id == setup.doing.id
    setup.client.update_task = lambda gid, data: asana_task(gid=gid)
    with patch("plane.app.asana_sync.engine.AsanaClient", return_value=setup.client):
        result = run_sync_pass(setup.sync.id)
    assert result["pushed"] == 1
    issue.refresh_from_db()
    assert issue.state_id == setup.doing.id
