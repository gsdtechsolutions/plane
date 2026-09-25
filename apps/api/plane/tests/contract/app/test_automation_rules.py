# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import transaction

from plane.db.models import (
    AutomationRule,
    AutomationExecution,
    Issue,
    IssueLabel,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)
from plane.app.serializers.issue import IssueCreateSerializer
from plane.api.serializers.issue import IssueSerializer
from plane.app.automations import executor  # register real signal receivers

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def isolate_external_delivery():
    # Keep scratch work items off live Redis channels and worker queues.
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Automation regression", identifier="AUTOREG", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    old = State.objects.create(name="Todo", group="unstarted", color="#555555", project=project)
    new = State.objects.create(name="Doing", group="started", color="#777777", project=project)
    issue = Issue.objects.create(name="Regression", project=project, state=old, priority="none")
    member = User.objects.create(email="automation-member@example.test", username="automation-member")
    WorkspaceMember.objects.create(workspace=workspace, member=member, role=15, is_active=True)
    ProjectMember.objects.create(project=project, member=member, role=15, is_active=True)
    return SimpleNamespace(project=project, issue=issue, old=old, new=new, member=member)


def rule(board, trigger="state_changed", actions=None):
    return AutomationRule.objects.create(
        project=board.project,
        name="Regression",
        trigger_type=trigger,
        trigger_value=None,
        actions=actions or [{"type": "set_priority", "value": "urgent"}],
    )


def update(board, serializer_class, data):
    serializer = serializer_class(
        board.issue,
        data=data,
        partial=True,
        context={"project_id": board.project.id, "workspace_id": board.project.workspace_id},
    )
    assert serializer.is_valid(), serializer.errors
    return serializer.save()


@pytest.mark.parametrize(
    "serializer_class,field",
    [
        (IssueCreateSerializer, "assignee_ids"),
        (IssueSerializer, "assignees"),
    ],
)
def test_bulk_replacement_detects_only_new_assignee(board, serializer_class, field):
    rule(board, "assignee_added")
    # No test wrapper transaction: this is the actual autocommit request shape.
    result = update(board, serializer_class, {field: [str(board.member.id)]})
    assert result.priority == "urgent"
    board.issue.refresh_from_db()
    assert board.issue.priority == "urgent"
    assert set(board.issue.assignees.values_list("id", flat=True)) == {board.member.id}
    # Same relation replacement must not count as adding the member again.
    Issue.objects.filter(pk=board.issue.pk).update(priority="low")
    board.issue.refresh_from_db()
    update(board, serializer_class, {field: [str(board.member.id)]})
    board.issue.refresh_from_db()
    assert board.issue.priority == "low"


@pytest.mark.parametrize(
    "serializer_class,field",
    [
        (IssueCreateSerializer, "state_id"),
        (IssueSerializer, "state"),
    ],
)
def test_real_persisted_transition_and_noop(board, serializer_class, field):
    rule(board)
    update(board, serializer_class, {field: str(board.new.id)})
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.new.id
    assert board.issue.priority == "urgent"
    Issue.objects.filter(pk=board.issue.pk).update(priority="low")
    board.issue.refresh_from_db()
    update(board, serializer_class, {field: str(board.new.id)})
    board.issue.refresh_from_db()
    assert board.issue.priority == "low"


def test_rule_permissions_and_foreign_action_validation(board, session_client, create_user):
    existing = rule(board)
    url = f"/api/workspaces/{board.project.workspace.slug}/projects/{board.project.id}/automations/rules/"
    payload = {
        "name": "Unauthorized",
        "trigger_type": "state_changed",
        "actions": [{"type": "set_priority", "value": "low"}],
    }
    session_client.force_authenticate(board.member)
    before = AutomationRule.objects.count()
    assert session_client.post(url, payload, format="json").status_code == 403
    assert session_client.post(f"{url}{existing.id}/toggle/", {}, format="json").status_code == 403
    assert session_client.patch(f"{url}{existing.id}/", {"name": "changed"}, format="json").status_code == 403
    assert session_client.delete(f"{url}{existing.id}/").status_code == 403
    assert AutomationRule.objects.count() == before
    existing.refresh_from_db()
    assert existing.is_active is True and existing.name == "Regression"
    # Same workspace, no membership in a private project: cannot inspect rules.
    other = Project.objects.create(name="Private", identifier="PRIV", workspace=board.project.workspace, network=0)
    private_url = f"/api/workspaces/{other.workspace.slug}/projects/{other.id}/automations/rules/"
    assert session_client.get(private_url).status_code in (403, 404)
    # Even an authorized admin cannot configure another project's state.
    foreign = State.objects.create(name="Foreign", group="started", color="#999999", project=other)
    session_client.force_authenticate(create_user)
    payload["actions"] = [{"type": "set_state", "value": str(foreign.id)}]
    assert session_client.post(url, payload, format="json").status_code == 400
    assert AutomationRule.objects.count() == before


def test_rolled_back_serializer_update_has_no_actions(board):
    rule(board)
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            update(board, IssueCreateSerializer, {"state_id": str(board.new.id)})
            raise RuntimeError("rollback")
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.old.id
    assert board.issue.priority == "none"
    # Ensure failed-request snapshots cannot leak into the next successful request.
    update(board, IssueCreateSerializer, {"state_id": str(board.new.id)})
    board.issue.refresh_from_db()
    assert board.issue.priority == "urgent"


def test_action_persistence_failure_does_not_leave_partial_rule(board):
    label = Label.objects.create(name="Automation label", project=board.project)
    selected = rule(
        board,
        actions=[
            {"type": "add_label", "value": str(label.id)},
            {"type": "set_priority", "value": "urgent"},
        ],
    )
    # Fail only during after-commit rule processing; the original user save
    # already happened before the patch starts. No action helper is mocked.
    failure = patch.object(Issue, "save", side_effect=RuntimeError("storage failure"))
    try:
        with transaction.atomic():
            update(board, IssueCreateSerializer, {"state_id": str(board.new.id)})
            failure.start()
    finally:
        failure.stop()
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.new.id
    assert board.issue.priority == "none"
    assert not IssueLabel.objects.filter(issue=board.issue, label=label).exists()
    assert AutomationExecution.objects.filter(rule=selected, issue=board.issue, status="failed").exists()


def test_duplicate_execution_is_inert(board):
    selected = rule(board)
    update(board, IssueCreateSerializer, {"state_id": str(board.new.id)})
    execution = AutomationExecution.objects.get(rule=selected, issue=board.issue)
    assert execution.status == "succeeded"
    Issue.objects.filter(pk=board.issue.pk).update(priority="low")
    executor.execute_execution(execution.pk)
    board.issue.refresh_from_db()
    assert board.issue.priority == "low"
    assert AutomationExecution.objects.filter(rule=selected, issue=board.issue).count() == 1


@pytest.mark.parametrize("change", ["disable", "delete"])
def test_rule_removed_before_callback_cannot_execute(board, change):
    selected = rule(board)
    with transaction.atomic():
        update(board, IssueCreateSerializer, {"state_id": str(board.new.id)})
        if change == "disable":
            selected.is_active = False
            selected.save(update_fields=["is_active"])
        else:
            selected.delete()
    board.issue.refresh_from_db()
    assert board.issue.priority == "none"


@pytest.mark.parametrize("value", ["invalid-uuid", str(uuid4())])
def test_invalid_or_missing_action_uuid_is_400(board, session_client, value):
    url = f"/api/workspaces/{board.project.workspace.slug}/projects/{board.project.id}/automations/rules/"
    response = session_client.post(
        url,
        {
            "name": "Invalid reference",
            "trigger_type": "state_changed",
            "actions": [{"type": "set_state", "value": value}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert not AutomationRule.objects.filter(name="Invalid reference").exists()


def test_guest_cannot_be_automation_assignee(board, session_client):
    ProjectMember.objects.filter(project=board.project, member=board.member).update(role=5)
    url = f"/api/workspaces/{board.project.workspace.slug}/projects/{board.project.id}/automations/rules/"
    response = session_client.post(
        url,
        {
            "name": "Guest assignment",
            "trigger_type": "state_changed",
            "actions": [{"type": "assign_member", "value": str(board.member.id)}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert not AutomationRule.objects.filter(name="Guest assignment").exists()


@pytest.mark.parametrize("trigger,property_name", [("state_changed", "state_id"), ("assignee_added", "assignee_ids")])
def test_board_bulk_changes_fire_once(board, session_client, trigger, property_name):
    selected = rule(board, trigger)
    value = str(board.new.id) if property_name == "state_id" else [str(board.member.id)]
    url = f"/api/workspaces/{board.project.workspace.slug}/projects/{board.project.id}/bulk-operation-issues/"
    payload = {"issue_ids": [str(board.issue.id)], "properties": {property_name: value}}
    assert session_client.post(url, payload, format="json").status_code == 204
    board.issue.refresh_from_db()
    assert board.issue.priority == "urgent"
    assert AutomationExecution.objects.filter(rule=selected, status="succeeded").count() == 1
    Issue.objects.filter(pk=board.issue.pk).update(priority="low")
    assert session_client.post(url, payload, format="json").status_code == 204
    board.issue.refresh_from_db()
    assert board.issue.priority == "low"
    assert AutomationExecution.objects.filter(rule=selected).count() == 1


def test_actions_persist_completion_without_cascade_or_bookkeeping_replay(board):
    from datetime import date
    import json
    from plane.bgtasks.issue_activities_task import issue_activity

    done = State.objects.create(name="Done", group="completed", color="#00aa00", project=board.project)
    added = Label.objects.create(name="Added", project=board.project)
    removed = Label.objects.create(name="Removed", project=board.project)
    IssueLabel.objects.create(issue=board.issue, label=removed, project=board.project)
    selected = rule(
        board,
        actions=[
            {"type": "set_state", "value": str(done.id)},
            {"type": "set_priority", "value": "high"},
            {"type": "set_due_date", "value": "2027-01-20"},
            {"type": "add_label", "value": str(added.id)},
            {"type": "remove_label", "value": str(removed.id)},
            {"type": "assign_member", "value": str(board.member.id)},
        ],
    )
    selected.trigger_value = board.new.id
    selected.save()
    downstream = rule(board, actions=[{"type": "set_priority", "value": "low"}])
    downstream.trigger_value = done.id
    downstream.save()
    assignment = rule(board, "assignee_added", [{"type": "set_priority", "value": "urgent"}])
    result = update(board, IssueCreateSerializer, {"state_id": str(board.new.id)})
    assert result.state_id == done.id and result.priority == "high"
    assert result.completed_at is not None and result.target_date == date(2027, 1, 20)
    assert set(IssueLabel.objects.filter(issue=result).values_list("label_id", flat=True)) == {added.id}
    assert set(IssueSerializer(result).data["labels"]) == {str(added.id)}
    assert set(result.assignees.values_list("id", flat=True)) == {board.member.id}
    assert not AutomationExecution.objects.filter(rule__in=[downstream, assignment]).exists()
    assert AutomationExecution.objects.get(rule=selected).status == "succeeded"
    # Run the real background activity task after the synchronous guard ended.
    issue_activity(
        type="issue.activity.updated",
        requested_data=json.dumps({"priority": "high"}),
        current_instance=json.dumps({"priority": "none"}),
        issue_id=str(result.id),
        actor_id=str(board.member.id),
        project_id=str(board.project.id),
        epoch=1,
    )
    result.refresh_from_db()
    assert result.priority == "high" and AutomationExecution.objects.count() == 1


def test_plain_model_state_save_triggers_rule(board):
    selected = rule(board)
    board.issue.state = board.new
    board.issue.save(update_fields=["state"])
    board.issue.refresh_from_db()
    assert board.issue.priority == "urgent"
    assert AutomationExecution.objects.get(rule=selected).status == "succeeded"


def test_realtime_publish_waits_for_commit_and_discards_rollback(board):
    with patch("plane.bgtasks.workitem_realtime._schedule_publish") as schedule:
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                board.issue.priority = "urgent"
                board.issue.save(update_fields=["priority"])
                schedule.assert_not_called()
                raise RuntimeError("rollback")
        schedule.assert_not_called()
        board.issue.refresh_from_db()
        with transaction.atomic():
            board.issue.priority = "high"
            board.issue.save(update_fields=["priority"])
            schedule.assert_not_called()
        schedule.assert_called_once()
        assert schedule.call_args.args[0] == str(board.issue.id)


def test_remove_label_only_action_emits_final_committed_relations(board):
    label = Label.objects.create(name="Remove me", project=board.project)
    IssueLabel.objects.create(issue=board.issue, label=label, project=board.project)
    rule(board, actions=[{"type": "remove_label", "value": str(label.id)}])
    with patch("plane.bgtasks.workitem_realtime._schedule_publish") as schedule:
        result = update(board, IssueCreateSerializer, {"state_id": str(board.new.id)})
        assert not IssueLabel.objects.filter(issue=result).exists()
        assert IssueSerializer(result).data["labels"] == []
        assert schedule.call_args.args[1]["label_ids"] == []


@pytest.mark.parametrize("stale_reference", ["state", "member", "action"])
def test_stale_rule_can_be_disabled_but_not_reenabled(board, session_client, stale_reference):
    from django.utils import timezone

    selected = rule(board)
    if stale_reference == "state":
        selected.trigger_value = board.new.id
        selected.save()
        State.objects.filter(pk=board.new.id).update(deleted_at=timezone.now())
    elif stale_reference == "member":
        selected.trigger_type = "assignee_added"
        selected.trigger_value = board.member.id
        selected.save()
        ProjectMember.objects.filter(project=board.project, member=board.member).update(is_active=False)
    else:
        label = Label.objects.create(name="Former action target", project=board.project)
        selected.actions = [{"type": "add_label", "value": str(label.id)}]
        selected.save()
        Label.objects.filter(pk=label.id).update(deleted_at=timezone.now())
    url = f"/api/workspaces/{board.project.workspace.slug}/projects/{board.project.id}/automations/rules/{selected.id}/"
    response = session_client.patch(url, {"is_active": False}, format="json")
    assert response.status_code == 200
    selected.refresh_from_db()
    assert selected.is_active is False
    response = session_client.patch(url, {"is_active": True}, format="json")
    assert response.status_code == 400
    selected.refresh_from_db()
    assert selected.is_active is False


def test_remove_absent_label_action_does_not_touch_issue_or_publish(board):
    label = Label.objects.create(name="Never attached", project=board.project)
    selected = rule(board, actions=[{"type": "remove_label", "value": str(label.id)}])
    board.issue.refresh_from_db()
    before = board.issue.updated_at
    execution = AutomationExecution.objects.create(rule=selected, issue=board.issue, event_id=uuid4())
    with patch("plane.bgtasks.workitem_realtime._schedule_publish") as schedule:
        executor.execute_execution(execution.pk)
        schedule.assert_not_called()
    board.issue.refresh_from_db()
    execution.refresh_from_db()
    assert board.issue.updated_at == before
    assert execution.status == "succeeded"
