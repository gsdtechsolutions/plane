# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from plane.app.ai_triage import tasks as triage_tasks
from plane.app.release_intelligence.provider import IntelligenceError
from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueLabel,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.ai_audit import AIActionAudit
from plane.db.models.ai_triage import AIIssueSuggestion

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def triage_routes(settings):
    """Mount the ai_triage endpoints for this test file only.

    This lane must not edit the shared plane/app/urls/__init__.py, so the
    routes are appended to a test-local root urlconf. The coordinator's
    include() line (see LANE-NOTES.md) replaces this fixture entirely."""
    import sys
    import types

    from django.urls import clear_url_caches, include, path

    from plane.app.ai_triage.urls import urlpatterns as triage_urls
    from plane.urls import urlpatterns as root_urls

    module = types.ModuleType("ai_triage_test_urls")
    module.urlpatterns = list(root_urls) + [path("api/", include(triage_urls))]
    module.handler404 = "plane.app.views.error_404.custom_404_view"
    sys.modules["ai_triage_test_urls"] = module
    settings.ROOT_URLCONF = "ai_triage_test_urls"
    clear_url_caches()
    yield
    sys.modules.pop("ai_triage_test_urls", None)
    clear_url_caches()


@pytest.fixture(autouse=True)
def boundaries(settings):
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def board(create_user):
    workspace = Workspace.objects.create(name="Triage", slug="triage", owner=create_user)
    WorkspaceMember.objects.create(workspace=workspace, member=create_user, role=20, is_active=True)
    project = Project.objects.create(name="Triage Project", identifier="TRI", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    member = User.objects.create(email="dev@triage.test", username="dev", password="x")
    WorkspaceMember.objects.create(workspace=workspace, member=member, role=15, is_active=True)
    ProjectMember.objects.create(project=project, member=member, role=15, is_active=True)
    # Workspace member without project membership: every endpoint must 403.
    outsider = User.objects.create(email="out@triage.test", username="out", password="x")
    WorkspaceMember.objects.create(workspace=workspace, member=outsider, role=5, is_active=True)
    state = State.objects.create(
        name="Todo", group="unstarted", color="#555555", project=project, sequence=15000, default=True
    )
    Label.objects.create(name="Bug", project=project, color="#ff0000")
    Label.objects.create(name="Feature", project=project, color="#00ff00")
    issue = Issue.objects.create(
        name="Login broken on Safari",
        project=project,
        state=state,
        description_html="<p>Users cannot sign in with Safari 18: the session cookie is dropped.</p>",
    )
    return SimpleNamespace(
        workspace=workspace,
        project=project,
        issue=issue,
        user=create_user,
        member=member,
        outsider=outsider,
        state=state,
    )


def model_json(payload):
    return {"text": json.dumps(payload), "model": "test-model-x"}


def suggestion(board, kind, payload, **kwargs):
    return AIIssueSuggestion.objects.create(
        workspace=board.workspace,
        project=board.project,
        issue=board.issue,
        kind=kind,
        payload=payload,
        **kwargs,
    )


def triage_url(board):
    return (
        f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/issues/{board.issue.id}"
        "/triage-suggestions/"
    )


def decide_url(board, row, action):
    return f"{triage_url(board)}{row.id}/{action}/"


# --- async task: the LLM pass ---


def test_triage_task_creates_reviewable_suggestions(board):
    payload = {
        "labels": ["Bug", "Label That Does Not Exist"],
        "assignee_email": "dev@triage.test",
        "priority": "high",
        "summary": "Safari drops the session cookie on login.",
        "confidence": 0.82,
    }
    with patch("plane.app.release_intelligence.provider.generate_text", return_value=model_json(payload)):
        triage_tasks.triage_issue(board.issue.id)

    rows = AIIssueSuggestion.objects.filter(issue=board.issue)
    assert rows.count() == 4  # one valid label + assignee + priority + summary
    by_kind = {row.kind: row for row in rows}
    assert by_kind["label"].payload == {"name": "Bug"}  # invented label dropped, existing kept
    assert by_kind["assignee"].payload == {"user_id": str(board.member.id), "email": "dev@triage.test"}
    assert by_kind["priority"].payload == {"priority": "high"}
    assert by_kind["summary"].payload == {"summary": "Safari drops the session cookie on login."}
    assert all(row.model == "test-model-x" for row in rows)
    assert all(row.status == "pending" for row in rows)
    assert by_kind["label"].confidence == 0.82

    audit = AIActionAudit.objects.get(action="issue.triage_suggest", entity_id=str(board.issue.id))
    assert audit.status == "success"
    assert audit.model == "test-model-x"
    assert audit.metadata["suggestions"] == 4


def test_triage_task_matches_labels_case_insensitively(board):
    payload = {
        "labels": ["bug", "FEATURE"],
        "assignee_email": None,
        "priority": None,
        "summary": "s",
        "confidence": 0.5,
    }
    with patch("plane.app.release_intelligence.provider.generate_text", return_value=model_json(payload)):
        triage_tasks.triage_issue(board.issue.id)

    names = sorted(
        AIIssueSuggestion.objects.filter(issue=board.issue, kind="label").values_list("payload", flat=True),
        key=lambda payload: payload["name"],
    )
    assert names == [{"name": "Bug"}, {"name": "Feature"}]  # canonical existing names, never invented


def test_triage_task_drops_unknown_labels(board):
    payload = {
        "labels": ["Ghost", "Phantom"],
        "assignee_email": None,
        "priority": "low",
        "summary": "needs triage",
        "confidence": 0.4,
    }
    with patch("plane.app.release_intelligence.provider.generate_text", return_value=model_json(payload)):
        triage_tasks.triage_issue(board.issue.id)
    assert AIIssueSuggestion.objects.filter(issue=board.issue, kind="label").count() == 0
    # the other kinds still produce reviewable suggestions
    assert AIIssueSuggestion.objects.filter(issue=board.issue, kind="priority").count() == 1
    assert AIIssueSuggestion.objects.filter(issue=board.issue, kind="summary").count() == 1


def test_triage_task_drops_non_member_email(board):
    payload = {
        "labels": [],
        "assignee_email": "stranger@elsewhere.test",
        "priority": None,
        "summary": "s",
        "confidence": 0.5,
    }
    with patch("plane.app.release_intelligence.provider.generate_text", return_value=model_json(payload)):
        triage_tasks.triage_issue(board.issue.id)
    assert AIIssueSuggestion.objects.filter(issue=board.issue, kind="assignee").count() == 0


def test_triage_task_drops_invalid_priority(board):
    payload = {
        "labels": [],
        "assignee_email": None,
        "priority": "critical",
        "summary": "s",
        "confidence": 0.5,
    }
    with patch("plane.app.release_intelligence.provider.generate_text", return_value=model_json(payload)):
        triage_tasks.triage_issue(board.issue.id)
    assert AIIssueSuggestion.objects.filter(issue=board.issue, kind="priority").count() == 0


def test_triage_task_parses_fenced_json(board):
    raw = {
        "text": "Here you go:\n```json\n"
        + json.dumps(
            {
                "labels": ["Bug"],
                "assignee_email": "dev@triage.test",
                "priority": "urgent",
                "summary": "Cookie dropped on Safari login.",
                "confidence": 0.9,
            }
        )
        + "\n```\nHope this helps!",
        "model": "test-model-x",
    }
    with patch("plane.app.release_intelligence.provider.generate_text", return_value=raw):
        triage_tasks.triage_issue(board.issue.id)
    assert AIIssueSuggestion.objects.filter(issue=board.issue).count() == 4


def test_triage_task_garbage_json_audits_error_and_creates_nothing(board):
    with patch(
        "plane.app.release_intelligence.provider.generate_text",
        return_value={"text": "The trash compactor scene is the best part.", "model": "test-model-x"},
    ):
        triage_tasks.triage_issue(board.issue.id)  # must not raise

    assert AIIssueSuggestion.objects.filter(issue=board.issue).count() == 0
    audit = AIActionAudit.objects.get(action="issue.triage_suggest", entity_id=str(board.issue.id))
    assert audit.status == "error"
    assert "JSON" in audit.error or "json" in audit.error


def test_triage_task_provider_failure_audits_error_and_creates_nothing(board):
    with patch(
        "plane.app.release_intelligence.provider.generate_text",
        side_effect=IntelligenceError("The configured AI provider could not complete this request."),
    ):
        triage_tasks.triage_issue(board.issue.id)  # must not raise

    assert AIIssueSuggestion.objects.filter(issue=board.issue).count() == 0
    audit = AIActionAudit.objects.get(action="issue.triage_suggest", entity_id=str(board.issue.id))
    assert audit.status == "error"
    assert "provider" in audit.error


def test_triage_task_skips_when_pending_suggestions_exist(board):
    suggestion(board, "summary", {"summary": "already waiting"})
    with patch("plane.app.release_intelligence.provider.generate_text") as generate:
        triage_tasks.triage_issue(board.issue.id)
    generate.assert_not_called()
    assert AIIssueSuggestion.objects.filter(issue=board.issue).count() == 1


def test_triage_task_ignores_missing_issue():
    with patch("plane.app.release_intelligence.provider.generate_text") as generate:
        triage_tasks.triage_issue(uuid.uuid4())
    generate.assert_not_called()


# --- maybe_enqueue_triage ---


def test_maybe_enqueue_triage_enqueues_only_on_create(board):
    with patch.object(triage_tasks.triage_issue, "delay") as delay:
        triage_tasks.maybe_enqueue_triage(str(board.issue.id), created=False)
        delay.assert_not_called()
        triage_tasks.maybe_enqueue_triage(str(board.issue.id), created=True)
        delay.assert_called_once_with(str(board.issue.id))


# --- GET endpoint ---


def test_list_orders_pending_first_then_newest(board, session_client):
    older = suggestion(
        board,
        "summary",
        {"summary": "old decided one"},
        status="accepted",
        decided_by=board.user,
        decided_at=timezone.now(),
    )
    pending = suggestion(board, "label", {"name": "Bug"})
    response = session_client.get(triage_url(board))
    assert response.status_code == 200
    ids = [str(row["id"]) for row in response.data["results"]]
    assert ids == [str(pending.id), str(older.id)]
    first = response.data["results"][0]
    assert first["kind"] == "label"
    assert first["payload"] == {"name": "Bug"}
    assert first["status"] == "pending"
    assert first["model"] in (None, "")


def test_list_requires_project_membership(board):
    client = APIClient()
    client.force_authenticate(user=board.outsider)  # workspace member, not project member
    assert client.get(triage_url(board)).status_code == 403


def test_list_unknown_issue_is_404(board, session_client):
    response = session_client.get(
        f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/issues/{uuid.uuid4()}"
        "/triage-suggestions/"
    )
    assert response.status_code == 404


# --- accept endpoint ---


def test_accept_label_suggestion_writes_direct_through_row(board, session_client):
    row = suggestion(board, "label", {"name": "bug"}, confidence=0.7)
    response = session_client.post(decide_url(board, row, "accept"))

    assert response.status_code == 200
    assert response.data["status"] == "accepted"
    assert str(response.data["decided_by"]) == str(board.user.id)
    # No new Label invented: the case-insensitive existing label is reused.
    assert Label.objects.filter(project=board.project).count() == 2
    through = IssueLabel.objects.filter(issue=board.issue)
    assert through.count() == 1
    link = through.get()
    assert link.label.name == "Bug"
    assert link.project_id == board.project.id
    assert link.workspace_id == board.workspace.id

    audit = AIActionAudit.objects.get(action="issue.triage_accept", entity_id=str(board.issue.id))
    assert audit.status == "success"
    assert audit.metadata["kind"] == "label"


def test_accept_assignee_suggestion_writes_direct_through_row(board, session_client):
    row = suggestion(board, "assignee", {"user_id": str(board.member.id), "email": "dev@triage.test"})
    response = session_client.post(decide_url(board, row, "accept"))

    assert response.status_code == 200
    through = IssueAssignee.objects.filter(issue=board.issue, assignee_id=board.member.id)
    assert through.count() == 1
    link = through.get()
    assert link.project_id == board.project.id
    assert link.workspace_id == board.workspace.id


def test_accept_assignee_rejects_user_no_longer_in_project(board, session_client):
    stranger = User.objects.create(email="gone@triage.test", username="gone", password="x")
    row = suggestion(board, "assignee", {"user_id": str(stranger.id), "email": "gone@triage.test"})
    response = session_client.post(decide_url(board, row, "accept"))
    assert response.status_code == 400
    row.refresh_from_db()
    assert row.status == "pending"
    assert IssueAssignee.objects.filter(issue=board.issue).count() == 0


def test_accept_priority_suggestion_updates_issue(board, session_client):
    row = suggestion(board, "priority", {"priority": "urgent"})
    response = session_client.post(decide_url(board, row, "accept"))
    assert response.status_code == 200
    board.issue.refresh_from_db()
    assert board.issue.priority == "urgent"
    # description untouched by the single-field update
    assert board.issue.description_stripped == (
        "Users cannot sign in with Safari 18: the session cookie is dropped."
    )


def test_accept_summary_suggestion_is_informational_only(board, session_client):
    row = suggestion(board, "summary", {"summary": "One-liner for humans."})
    response = session_client.post(decide_url(board, row, "accept"))
    assert response.status_code == 200
    assert response.data["status"] == "accepted"
    board.issue.refresh_from_db()
    assert board.issue.priority == "none"
    assert IssueLabel.objects.filter(issue=board.issue).count() == 0
    assert IssueAssignee.objects.filter(issue=board.issue).count() == 0


def test_accept_twice_conflicts(board, session_client):
    row = suggestion(board, "label", {"name": "Bug"})
    assert session_client.post(decide_url(board, row, "accept")).status_code == 200
    assert session_client.post(decide_url(board, row, "accept")).status_code == 409
    assert IssueLabel.objects.filter(issue=board.issue).count() == 1


def test_accept_requires_project_membership(board):
    row = suggestion(board, "label", {"name": "Bug"})
    client = APIClient()
    client.force_authenticate(user=board.outsider)
    assert client.post(decide_url(board, row, "accept")).status_code == 403
    row.refresh_from_db()
    assert row.status == "pending"


def test_accept_unknown_suggestion_is_404(board, session_client):
    response = session_client.post(
        f"{triage_url(board)}{uuid.uuid4()}/accept/"
    )
    assert response.status_code == 404


# --- dismiss endpoint ---


def test_dismiss_marks_row_and_audits(board, session_client):
    row = suggestion(board, "priority", {"priority": "low"})
    response = session_client.post(decide_url(board, row, "dismiss"))

    assert response.status_code == 200
    assert response.data["status"] == "dismissed"
    row.refresh_from_db()
    assert row.status == "dismissed"
    assert row.decided_by_id == board.user.id
    assert row.decided_at is not None
    board.issue.refresh_from_db()
    assert board.issue.priority == "none"  # dismiss never writes to the issue

    audit = AIActionAudit.objects.get(action="issue.triage_dismiss", entity_id=str(board.issue.id))
    assert audit.status == "success"
    assert audit.metadata["kind"] == "priority"


def test_dismiss_requires_project_membership(board):
    row = suggestion(board, "summary", {"summary": "s"})
    client = APIClient()
    client.force_authenticate(user=board.outsider)
    assert client.post(decide_url(board, row, "dismiss")).status_code == 403
