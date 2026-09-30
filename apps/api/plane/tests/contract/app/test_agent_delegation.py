# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import (
    Issue,
    IssueAssignee,
    IssueComment,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
    WorkspaceMember,
)
from plane.db.models.agent_delegation import DelegationRun
from plane.db.models.ai_audit import AIActionAudit

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

# Exercise the lane's real URL patterns without changing shared URL wiring.
urlpatterns = [path("api/", include("plane.app.agent_delegation.urls"))]
KEY = {"HTTP_X_RUNNER_KEY": "test-runner-key"}


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    from django.conf import settings

    # Parallel lanes share Postgres; --create-db must only replace our database.
    settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_plane_delegate_backend"


@pytest.fixture(autouse=True)
def boundaries(settings, monkeypatch):
    settings.ROOT_URLCONF = __name__
    settings.AGENT_RUNNER_KEY = "test-runner-key"
    monkeypatch.delenv("AGENT_RUNNER_KEY", raising=False)
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def board():
    user = User.objects.create(email="member@example.com", username="member@example.com")
    workspace = Workspace.objects.create(name="Delegation", slug="delegation", owner=user)
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=20, is_active=True)
    project = Project.objects.create(name="Agent", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=user, role=20, is_active=True)
    state = State.objects.create(name="Todo", group="unstarted", color="#555555", project=project, default=True)
    issue = Issue.objects.create(
        name="Build feature", description_html="<p>Ship safely &amp; quickly</p>", project=project, state=state
    )
    client = APIClient()
    client.force_authenticate(user=user)
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/agent-delegations/"
    return SimpleNamespace(user=user, workspace=workspace, project=project, issue=issue, client=client, url=url)


def queued(board, **kwargs):
    return DelegationRun.objects.create(
        workspace=board.workspace, project=board.project, issue=board.issue, created_by=board.user, **kwargs
    )


def event(run, status, runner_id="runner-one", **kwargs):
    return APIClient().post(
        f"/api/agent-runner/{run.id}/events/",
        {"status": status, "runner_id": runner_id, **kwargs},
        format="json",
        **KEY,
    )


def test_member_creates_delegation_comment_and_audit(board):
    response = board.client.post(board.url, {"instructions": "Add tests"}, format="json")
    assert response.status_code == 201
    run = DelegationRun.objects.get(id=response.data["id"])
    assert run.status == "queued" and run.instructions == "Add tests" and run.created_by == board.user
    assert response.data["issue"] == {"id": str(board.issue.id), "display": "DEV-1"}
    assert response.data["created_by"] == {"id": str(board.user.id), "email": board.user.email}
    comment = IssueComment.objects.get(issue=board.issue)
    assert f"run {str(run.id)[:8]} queued." in comment.comment_stripped
    assert comment.actor == board.user and comment.created_by == board.user
    audit = AIActionAudit.objects.get(action="agent.delegate")
    assert audit.entity_id == str(board.issue.id) and audit.actor == board.user


def test_creation_defaults_and_rejects_invalid_instructions(board):
    assert board.client.post(board.url, {}, format="json").status_code == 201
    for value in ["x" * 8001, 123, None, ["text"]]:
        assert board.client.post(board.url, {"instructions": value}, format="json").status_code == 400
    assert DelegationRun.objects.count() == 1


def test_creation_requires_active_project_membership(board):
    outsider = User.objects.create(email="outsider@example.com", username="outsider@example.com")
    board.client.force_authenticate(user=outsider)
    assert board.client.post(board.url, {}, format="json").status_code == 403
    WorkspaceMember.objects.create(workspace=board.workspace, member=outsider, role=15, is_active=True)
    assert board.client.post(board.url, {}, format="json").status_code == 403
    board.client.force_authenticate(user=None)
    assert board.client.post(board.url, {}, format="json").status_code in (401, 403)
    assert not DelegationRun.objects.exists()


def test_claim_auth_oldest_context_and_empty_queue(board):
    client = APIClient()
    first, second = queued(board, instructions="First"), queued(board)
    DelegationRun.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(minutes=1))
    IssueAssignee.objects.create(
        issue=board.issue, assignee=board.user, project=board.project, workspace=board.workspace
    )
    url = "/api/agent-runner/claim/"
    assert client.post(url, {"runner_id": "runner-one"}, format="json").status_code == 403
    assert client.post(url, {"runner_id": "runner-one"}, format="json", HTTP_X_RUNNER_KEY="wrong").status_code == 403
    assert client.post(url, {}, format="json", **KEY).status_code == 400
    response = client.post(url, {"runner_id": "runner-one"}, format="json", **KEY)
    assert response.status_code == 200 and response.data["id"] == str(first.id)
    context = response.data["issue"]
    assert context["issue_display"] == "DEV-1"
    assert context["workspace_slug"] == board.workspace.slug
    assert context["project_identifier"] == "DEV" and context["state_name"] == "Todo"
    assert context["assignee_emails"] == [board.user.email] and context["instructions"] == "First"
    assert context["branch"] == f"agent/dev-1-{str(first.id)[:8]}"
    assert context["repo"] == "gsdtechsolutions/plane"
    first.refresh_from_db()
    assert first.status == "claimed" and first.claimed_at and first.runner_id == "runner-one"
    assert first.branch == context["branch"]
    response = client.post(url, {"runner_id": "runner-two"}, format="json", **KEY)
    assert response.data["id"] == str(second.id)
    assert client.post(url, {"runner_id": "runner-three"}, format="json", **KEY).status_code == 204


def test_events_progress_comments_audits_and_idempotent_retry(board):
    run = queued(board, status="claimed", runner_id="runner-one")
    assert event(run, "running").status_code == 200
    run.refresh_from_db()
    assert run.started_at is not None
    pr = "https://github.com/gsdtechsolutions/plane/pull/123"
    response = event(run, "pr_opened", pr_url=pr, pr_number=123, branch="agent/dev-1-example")
    assert response.status_code == 200 and response.data["pr_url"] == pr and response.data["pr_number"] == 123
    assert IssueComment.objects.filter(issue=board.issue, comment_stripped__contains="opened draft PR").count() == 1
    assert AIActionAudit.objects.filter(action="agent.pr_opened").count() == 1
    assert event(run, "pr_opened", pr_url=pr).status_code == 200
    assert IssueComment.objects.filter(issue=board.issue).count() == 1
    assert event(run, "completed", result_excerpt="Tests pass").status_code == 200
    run.refresh_from_db()
    assert run.finished_at is not None and run.result_excerpt == "Tests pass"
    assert IssueComment.objects.filter(issue=board.issue, comment_stripped__contains="ready for review").exists()
    assert AIActionAudit.objects.filter(action="agent.completed").exists()


def test_events_reject_invalid_transition_and_wrong_owner(board):
    run = queued(board)
    assert event(run, "completed").status_code == 403
    run.runner_id = "runner-one"
    run.save()
    assert event(run, "completed").status_code == 409
    run.status = "claimed"
    run.save()
    assert event(run, "running", runner_id="wrong-runner").status_code == 403
    assert event(run, "running", runner_id="").status_code == 400
    assert event(run, "running", pr_url="javascript:alert(1)").status_code == 400
    assert event(run, "running", pr_number=-1).status_code == 400
    assert event(run, "queued").status_code == 400
    assert (
        APIClient()
        .post(f"/api/agent-runner/{run.id}/events/", {"runner_id": "runner-one", "status": "running"}, format="json")
        .status_code
        == 403
    )


@pytest.mark.parametrize("initial", ["claimed", "running", "pr_opened"])
def test_failure_records_escaped_comment_and_error_audit(board, initial):
    run = queued(board, status=initial, runner_id="runner-one")
    assert event(run, "failed", error="Failed <script> & stopped").status_code == 200
    run.refresh_from_db()
    assert run.finished_at and run.status == "failed"
    comment = IssueComment.objects.get(issue=board.issue)
    assert "&lt;script&gt; &amp;" in comment.comment_html
    audit = AIActionAudit.objects.get(action="agent.failed")
    assert audit.status == AIActionAudit.Status.ERROR and audit.error == run.error
    assert event(run, "running").status_code == 409


def test_no_changes_and_cancellation(board):
    run = queued(board, status="running", runner_id="runner-one")
    assert event(run, "completed", pr_url="https://github.com/gsdtechsolutions/plane/pull/10").status_code == 409
    assert event(run, "completed", result_excerpt="Agent made no changes.").status_code == 200
    run.refresh_from_db()
    assert run.finished_at
    assert "PR ready" not in IssueComment.objects.get(issue=board.issue).comment_stripped
    other = queued(board, status="claimed", runner_id="runner-one")
    assert event(other, "cancelled").status_code == 200
    other.refresh_from_db()
    assert other.finished_at


def test_health_key_unset_and_invalid(settings):
    client = APIClient()
    url = "/api/agent-runner/health/"
    assert client.get(url, **KEY).data == {"ok": True}
    missing = client.get(url)
    wrong = client.get(url, HTTP_X_RUNNER_KEY="wrong")
    assert missing.status_code == wrong.status_code == 403
    settings.AGENT_RUNNER_KEY = ""
    unset = client.get(url, **KEY)
    assert unset.status_code == 403 and unset.data == wrong.data


def test_list_detail_and_scope(board):
    first, second = queued(board), queued(board)
    foreign_project = Project.objects.create(name="Other", identifier="OTHER", workspace=board.workspace)
    foreign_issue = Issue.objects.create(name="Hidden", project=foreign_project)
    foreign_run = DelegationRun.objects.create(workspace=board.workspace, project=foreign_project, issue=foreign_issue)
    foreign_workspace = Workspace.objects.create(name="Elsewhere", slug="elsewhere", owner=board.user)
    project = Project.objects.create(name="Elsewhere", identifier="ELSE", workspace=foreign_workspace)
    issue = Issue.objects.create(name="Elsewhere issue", project=project)
    DelegationRun.objects.create(workspace=foreign_workspace, project=project, issue=issue)
    response = board.client.get(board.url, {"offset": 0, "limit": 1})
    assert response.status_code == 200 and response.data["count"] == 2
    assert [row["id"] for row in response.data["results"]] == [str(second.id)]
    assert board.client.get(board.url, {"offset": 1, "limit": 1}).data["results"][0]["id"] == str(first.id)
    assert board.client.get(board.url + f"{first.id}/").status_code == 200
    assert board.client.get(board.url + f"{foreign_run.id}/").status_code == 404
    wrong_issue_url = board.url.replace(str(board.issue.id), str(foreign_issue.id))
    assert board.client.post(wrong_issue_url, {}, format="json").status_code == 404
    assert board.client.get(wrong_issue_url).status_code == 404
