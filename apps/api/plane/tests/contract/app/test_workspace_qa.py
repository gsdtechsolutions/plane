# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import include, path
from rest_framework.test import APIClient

from plane.app.release_intelligence.provider import IntelligenceError
from plane.db.models import AIActionAudit, Issue, IssueAssignee, IssueComment, Project, ProjectMember, State, User, WorkspaceMember

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

ASK_URL = "/api/workspaces/test-workspace/ask/"

# The shared plane/app/urls/__init__.py is wired only after lane merge, so the
# tests ship their own root urlconf scoped to this lane's routes.
urlpatterns = [path("api/", include("plane.app.workspace_qa.urls"))]


@pytest.fixture(autouse=True)
def boundaries(settings):
    settings.ROOT_URLCONF = __name__
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Auth", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    state = State.objects.create(name="Done", group="completed", color="#46A758", project=project, sequence=35000)
    issue = Issue.objects.create(
        name="Fix login flow",
        project=project,
        state=state,
        priority="urgent",
        description_html="<p>Users cannot log in on mobile.</p>",
    )
    comment = IssueComment.objects.create(
        project=project,
        workspace=workspace,
        issue=issue,
        actor=create_user,
        comment_html="<p>Deployed the fix to production last week.</p>",
    )
    return SimpleNamespace(
        workspace=workspace,
        user=create_user,
        project=project,
        state=state,
        issue=issue,
        comment=comment,
    )


def ask(client, body):
    return client.post(ASK_URL, body, format="json")


# --- happy path ---
def test_ask_answers_with_citations(board, session_client):
    with patch(
        "plane.app.workspace_qa.api.generate_text",
        return_value={"text": "FR-14 is done. [KEY-1]", "model": "openai/test"},
    ) as provider:
        response = ask(session_client, {"question": "what is the status of login"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] is None
    assert "done" in data["answer"]
    assert data["model"] == "openai/test"
    provider.assert_called_once()
    references = data["references"]
    assert len(references) == 1
    reference = references[0]
    assert reference["display"] == f"DEV-{board.issue.sequence_id}-1"
    assert reference["name"] == "Fix login flow"
    assert reference["state"] == "Done"
    assert reference["state_group"] == "completed"
    assert reference["url"] == f"/test-workspace/projects/{board.project.id}/issues/{board.issue.id}"
    # the LLM sources carried the display marker
    sources = provider.call_args.args[1]
    assert sources[0]["content"].startswith(f"[DEV-{board.issue.sequence_id}-1]")
    assert audit_rows(board).count() == 1
    row = audit_rows(board).first()
    assert row.status == "success"
    assert row.input_excerpt == "what is the status of login"
    assert row.model == "openai/test"


# --- zero matches: no LLM call ---
def test_ask_without_matches_skips_llm(board, session_client):
    with patch("plane.app.workspace_qa.api.generate_text") as provider:
        response = ask(session_client, {"question": "zzzqqq unrelated gibberish"})
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] is None
    assert data["references"] == []
    assert data["message"] == "No matching issues found. Try different words."
    provider.assert_not_called()
    row = audit_rows(board).first()
    assert row is not None
    assert row.output_excerpt == "no_matches"


# --- provider unconfigured: 503 + audit ERROR ---
def test_ask_returns_503_when_ai_unconfigured(board, session_client):
    with patch(
        "plane.app.workspace_qa.api.generate_text",
        side_effect=IntelligenceError("Configure an AI API key and model in instance settings."),
    ):
        response = ask(session_client, {"question": "what is the status of login"})
    assert response.status_code == 503
    assert response.json() == {"error": "ai_unconfigured"}
    row = audit_rows(board).first()
    assert row.status == "error"
    assert row.input_excerpt == "what is the status of login"


# --- auth ---
def test_ask_requires_authentication(board):
    response = ask(APIClient(), {"question": "what is the status of login"})
    assert response.status_code in (401, 403)


def test_ask_rejects_non_members(board, create_user):
    outsider = User.objects.create(email="outsider@plane.so", username="outsider", first_name="Out")
    outsider.set_password("outsider-password")
    outsider.save()
    client = APIClient()
    client.force_authenticate(user=outsider)
    assert not WorkspaceMember.objects.filter(workspace=board.workspace, member=outsider).exists()
    response = ask(client, {"question": "what is the status of login"})
    assert response.status_code == 403


# --- input validation ---
@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "ok"}, {"question": 42}])
def test_ask_rejects_invalid_questions(board, session_client, body):
    with patch("plane.app.workspace_qa.api.generate_text") as provider:
        response = ask(session_client, body)
    assert response.status_code == 400
    provider.assert_not_called()


# --- tokenization sanity: stopwords only must not 500 ---
@pytest.mark.parametrize("question", ["the is a an", "what was it for"])
def test_ask_stopwords_only_is_handled(board, session_client, question):
    with patch("plane.app.workspace_qa.api.generate_text") as provider:
        response = ask(session_client, {"question": question})
    assert response.status_code == 400
    provider.assert_not_called()


# --- search reaches comments and honors project filter ---
def test_ask_matches_comment_text(board, session_client):
    with patch(
        "plane.app.workspace_qa.api.generate_text",
        return_value={"text": "Shipped last week. [KEY-1]", "model": "openai/test"},
    ):
        response = ask(session_client, {"question": "when did production deploy happen"})
    assert response.status_code == 200
    assert response.json()["references"][0]["id"] == str(board.issue.id)


def test_ask_project_filter_excludes_other_projects(board, session_client, workspace):
    other_project = Project.objects.create(name="Billing", identifier="BILL", workspace=workspace)
    Issue.objects.create(
        name="Fix login flow",
        project=other_project,
        description_html="<p>Another login flow.</p>",
    )
    with patch(
        "plane.app.workspace_qa.api.generate_text",
        return_value={"text": "Answer. [KEY-1]", "model": "openai/test"},
    ):
        response = ask(session_client, {"question": "what is the status of login", "project_id": str(other_project.id)})
    assert response.status_code == 200
    references = response.json()["references"]
    assert all(reference["display"].startswith("BILL-") for reference in references)
    assert audit_rows(board).count() == 1


def test_issue_assignees_render_in_sources(board, session_client, create_user):
    IssueAssignee.objects.create(
        project=board.project, workspace=board.workspace, issue=board.issue, assignee=create_user
    )
    with patch(
        "plane.app.workspace_qa.api.generate_text",
        return_value={"text": "Assigned to you. [KEY-1]", "model": "openai/test"},
    ) as provider:
        response = ask(session_client, {"question": "who is fixing login"})
    assert response.status_code == 200
    sources = provider.call_args.args[1]
    assert create_user.email in sources[0]["content"]


def audit_rows(board):
    return AIActionAudit.objects.filter(workspace=board.workspace, action="workspace.ask")
