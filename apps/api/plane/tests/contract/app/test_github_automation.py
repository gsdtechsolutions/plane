# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.utils import timezone
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember, State, User, WorkspaceMember
from plane.db.models.github_delivery import (
    GitHubApp,
    GitHubCommit,
    GitHubCommitIssueLink,
    GitHubConnection,
    GitHubIssueLink,
    GitHubAutomationRule,
    GitHubPullRequest,
    GitHubRepositoryMapping,
    GitHubWebhookDelivery,
)
from plane.app.github_delivery import services
from plane.app.github_delivery.crypto import encrypt_secret
from plane.app.github_delivery.tasks import backfill_github_workspace

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def boundaries(settings):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.GITHUB_DELIVERY = {"BASE_URL": "http://localhost:3002"}
    boundaries.pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    with patch("plane.bgtasks.workitem_realtime._schedule_publish") as publish, patch(
        "celery.app.task.Task.apply_async"
    ) as queue:
        yield SimpleNamespace(publish=publish, queue=queue)


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Development", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    todo = State.objects.create(name="Todo", group="unstarted", color="#555555", project=project)
    done = State.objects.create(name="Done", group="completed", color="#005511", project=project)
    issue = Issue.objects.create(name="Build feature", project=project, state=todo)
    app = GitHubApp.objects.create(
        host="github.com",
        app_id=123,
        slug="board-test",
        client_id="Iv.test",
        client_secret=encrypt_secret("test-secret"),
        private_key=encrypt_secret(boundaries.pem),
        webhook_secret=encrypt_secret("automation-test-secret"),
    )
    connection = GitHubConnection.objects.create(
        workspace=workspace,
        app=app,
        host="github.com",
        installation_id=456,
        account_login="team",
        github_user_id=7,
        connected_by=create_user,
        authorized_repository_ids=[789, 555, 556],
    )
    mapping = GitHubRepositoryMapping.objects.create(
        connection=connection, project=project, repository_id=789, full_name="team/repo", is_private=True
    )
    return SimpleNamespace(
        project=project,
        todo=todo,
        done=done,
        issue=issue,
        connection=connection,
        mapping=mapping,
        workspace=workspace,
        user=create_user,
        app=app,
    )


def root(board):
    return f"/api/workspaces/{board.workspace.slug}/github-delivery/"


def automation_url(board):
    return f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/github-delivery/automation/"


def pr_payload(board, number=9, repository_id=789, **overrides):
    return {
        "id": 900 + number,
        "number": number,
        "title": f"Build DEV-{board.issue.sequence_id}",
        "body": "Private evidence",
        "base": {"repo": {"id": repository_id}},
        "head": {"ref": "feature/DEV-999"},
        "state": "open",
        "updated_at": "2026-09-25T12:00:00Z",
        **overrides,
    }


def merged_payload(board, number=9, repository_id=789, base="main", **overrides):
    overrides.setdefault("updated_at", "2026-09-25T12:30:00Z")
    payload = pr_payload(
        board,
        number,
        repository_id,
        state="closed",
        merged=True,
        merged_at="2026-09-25T12:30:00Z",
        **overrides,
    )
    payload["base"] = {"repo": {"id": repository_id}, "ref": base}
    return payload


def add_rule(board, state=None, assignee=None, base_branch="", require_all_merged=True, enabled=True):
    return GitHubAutomationRule.objects.create(
        project=board.project,
        enabled=enabled,
        base_branch=base_branch,
        target_state=state or board.done,
        assignee=assignee,
        require_all_merged=require_all_merged,
    )


class HTTP:
    def __init__(self, data, status=200):
        self.status_code = status
        self.data = data

    def json(self):
        return self.data


def github_http(method, url, **kwargs):
    assert kwargs["allow_redirects"] is False
    path = urlsplit(url).path
    if path == "/app/installations/456/access_tokens":
        assert kwargs["json"]["permissions"] == {"metadata": "read", "pull_requests": "read", "contents": "read"}
        return HTTP({"token": "transient-installation-token"})
    if path == "/installation/repositories":
        return HTTP(
            {
                "total_count": 3,
                "repositories": [
                    {"id": 789, "full_name": "team/repo", "private": True},
                    {"id": 555, "full_name": "team/other", "private": False},
                    {"id": 556, "full_name": "team/unmapped", "private": False},
                ],
            }
        )
    raise AssertionError((method, path))


# ---------------------------------------------------------------------------
# Feature 1: auto-move when the work merges
# ---------------------------------------------------------------------------


def test_automation_disabled_and_partial_merges_do_not_move(board, boundaries):
    boundaries.publish.reset_mock()
    services.upsert_pull_request(board.mapping, pr_payload(board, number=8))
    services.upsert_pull_request(board.mapping, merged_payload(board, number=9))
    board.issue.refresh_from_db()
    # Without an enabled automation the merge is only evidence.
    assert board.issue.state_id == board.todo.id
    assert GitHubIssueLink.objects.filter(issue=board.issue).count() == 2
    add_rule(board)
    # A linked pull request still open keeps the work item in place.
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=10, updated_at="2026-09-25T13:00:00Z", title=f"Also DEV-{board.issue.sequence_id}"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.todo.id
    boundaries.publish.assert_not_called()


def test_automation_moves_issue_when_last_linked_pr_merges(board, boundaries):
    add_rule(board)
    second = State.objects.create(name="In Progress", group="started", color="#0055aa", project=board.project)
    board.issue.state = second
    board.issue.save(update_fields=["state", "updated_at"])
    boundaries.publish.reset_mock()
    # Two linked pull requests: the first merge holds while the other is open.
    services.upsert_pull_request(board.mapping, pr_payload(board, number=8))
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=9, updated_at="2026-09-25T13:00:00Z"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == second.id
    boundaries.publish.assert_not_called()
    # The last linked pull request merging completes the work.
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=8, updated_at="2026-09-25T13:30:00Z"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.done.id
    assert boundaries.publish.call_count == 1
    assert boundaries.publish.call_args[0][0] == str(board.issue.id)


def test_remerged_pr_does_not_move_again(board, boundaries):
    add_rule(board)
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=9, updated_at="2026-09-25T12:30:00Z"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.done.id
    board.issue.state = board.todo
    board.issue.save(update_fields=["state", "updated_at"])
    boundaries.publish.reset_mock()
    # Re-delivering the same merged pull request (an edit, a label, a fresh sync)
    # is not a new merge: the automation stays quiet.
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=9, updated_at="2026-09-25T14:00:00Z", title=f"Edited DEV-{board.issue.sequence_id}"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.todo.id
    boundaries.publish.assert_not_called()


def test_automation_respects_suppressed_links(board, boundaries):
    add_rule(board)
    open_pr = services.upsert_pull_request(board.mapping, pr_payload(board, number=8))
    # An open linked pull request blocks the verdict on the first merge.
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=9, updated_at="2026-09-25T13:00:00Z"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.todo.id
    # A user-removed link no longer counts: removing the open pull request's
    # link lets the next merge complete the work.
    GitHubIssueLink.objects.filter(issue=board.issue, pull_request=open_pr).update(is_suppressed=True)
    boundaries.publish.reset_mock()
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=10, updated_at="2026-09-25T13:30:00Z", title=f"Also DEV-{board.issue.sequence_id}"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.done.id
    assert boundaries.publish.call_count == 1


def test_automation_rules_crud_permissions_and_validation(board, session_client):
    WorkspaceMember.objects.filter(workspace=board.workspace, member=board.user).update(role=15)
    ProjectMember.objects.filter(project=board.project, member=board.user).update(role=15)
    assert session_client.get(automation_url(board)).status_code == 200
    assert session_client.post(automation_url(board), {}, format="json").status_code == 403
    ProjectMember.objects.filter(project=board.project, member=board.user).update(role=20)
    # A rule needs at least one action.
    assert session_client.post(automation_url(board), {"base_branch": "main"}, format="json").status_code == 400
    # Foreign states and non-member assignees are rejected.
    assert (
        session_client.post(
            automation_url(board), {"target_state_id": str(uuid4())}, format="json"
        ).status_code
        == 400
    )
    assert (
        session_client.post(automation_url(board), {"assignee_id": str(uuid4())}, format="json").status_code == 400
    )
    response = session_client.post(
        automation_url(board),
        {"base_branch": "main", "target_state_id": str(board.done.id), "assignee_id": str(board.user.id)},
        format="json",
    )
    assert response.status_code == 201, response.data
    rule = response.data
    assert rule["base_branch"] == "main" and rule["require_all_merged"] is True and rule["enabled"] is True
    listing = session_client.get(automation_url(board)).data["rules"]
    assert [item["id"] for item in listing] == [rule["id"]]
    detail = automation_url(board) + f"rules/{rule['id']}/"
    patched = session_client.patch(detail, {"enabled": False, "base_branch": ""}, format="json")
    assert patched.status_code == 200 and patched.data["enabled"] is False and patched.data["base_branch"] == ""
    assert session_client.delete(detail).status_code == 204
    assert session_client.get(automation_url(board)).data["rules"] == []

def test_automation_failure_never_breaks_ingestion(board):
    add_rule(board)
    with patch.object(
        GitHubAutomationRule.objects, "filter", side_effect=RuntimeError("automation exploded")
    ):
        pr = services.upsert_pull_request(board.mapping, merged_payload(board, number=9))
    assert pr.merged_at is not None
    assert GitHubIssueLink.objects.filter(issue=board.issue, pull_request=pr).count() == 1


# ---------------------------------------------------------------------------
# Feature 2: deep backfill sweep
# ---------------------------------------------------------------------------


def backfill_http(**repos):
    """Serve the sweep: installation repositories plus per-repo pulls and commits."""

    def handler(method, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        parts = urlsplit(url)
        page = int(parse_qs(parts.query).get("page", ["1"])[0])
        if parts.path in ("/installation/repositories", "/app/installations/456/access_tokens"):
            return github_http(method, url, **kwargs)
        for full_name, spec in repos.items():
            if parts.path == f"/repos/{full_name}/pulls":
                assert "state=all" in parts.query
                return HTTP(spec["pulls"][page - 1] if page <= len(spec["pulls"]) else [])
            if parts.path == f"/repos/{full_name}/commits":
                return HTTP(spec["commits"][page - 1] if page <= len(spec["commits"]) else [])
        raise AssertionError((method, parts.path))

    return handler


def rest_commit(sha, message, when="2026-09-20T09:00:00Z"):
    return {
        "sha": sha,
        "commit": {"message": message, "author": {"name": "Dev One", "date": when}},
        "author": {"login": "devone"},
        "html_url": f"https://github.com/team/repo/commit/{sha}",
    }


def test_backfill_maps_repos_to_the_dominant_project(board):
    second = Project.objects.create(name="Operations", identifier="OPS", workspace=board.workspace)
    ops_state = State.objects.create(name="Todo", group="unstarted", color="#555555", project=second)
    ops_issue = Issue.objects.create(name="Operate", project=second, state=ops_state)
    ops_key = f"OPS-{ops_issue.sequence_id}"
    with patch(
        "requests.request",
        side_effect=backfill_http(
            **{
                "team/repo": {
                    "pulls": [
                        [
                            pr_payload(board),
                            merged_payload(board, number=8, repository_id=789, title="Unrelated refactor"),
                        ]
                    ],
                    "commits": [[rest_commit("a" * 40, f"Fix DEV-{board.issue.sequence_id} properly")]],
                },
                "team/other": {
                    "pulls": [[merged_payload(board, number=3, repository_id=555, title=f"{ops_key} rollout")]],
                    "commits": [[rest_commit("b" * 40, f"chore: {ops_key} cleanup")]],
                },
                "team/unmapped": {"pulls": [[]], "commits": [[]]},
            }
        ),
    ):
        backfill_github_workspace(str(board.workspace.id))
    # Repositories land on the project they mention most; zero-hit repos stay unmapped.
    assert GitHubRepositoryMapping.objects.filter(repository_id=789, project_id=board.project.id).exists()
    other = GitHubRepositoryMapping.objects.get(repository_id=555)
    assert other.project_id == second.id and other.full_name == "team/other"
    assert other.is_auto is True and other.sync_status == "synced" and other.last_synced_at is not None
    assert not GitHubRepositoryMapping.objects.filter(repository_id=556).exists()
    # The pre-existing manual mapping keeps its project and its history is ingested.
    board.mapping.refresh_from_db()
    assert board.mapping.project_id == board.project.id and board.mapping.sync_error == ""
    assert board.mapping.sync_status == "synced" and board.mapping.last_synced_at is not None
    assert GitHubPullRequest.objects.filter(mapping=board.mapping, number__in=[8, 9]).count() == 2
    commit = GitHubCommit.objects.get(mapping=board.mapping, sha="a" * 40)
    assert GitHubCommitIssueLink.objects.filter(issue=board.issue, commit=commit).exists()
    assert GitHubIssueLink.objects.filter(issue=board.issue, pull_request__number=9).exists()
    assert GitHubIssueLink.objects.filter(issue=ops_issue, pull_request__mapping=other, pull_request__number=3).exists()


def test_backfill_records_repo_failure_and_continues(board):
    healthy = backfill_http(
        **{
            "team/repo": {"pulls": [[pr_payload(board)]], "commits": [[]]},
            "team/unmapped": {"pulls": [[]], "commits": [[]]},
        }
    )

    def failing_http(method, url, **kwargs):
        if urlsplit(url).path == "/repos/team/other/pulls":
            raise RuntimeError("network failure for this repository")
        return healthy(method, url, **kwargs)

    with patch("requests.request", side_effect=failing_http):
        backfill_github_workspace(str(board.workspace.id))
    # The healthy repository was still swept and linked; the failing one left no mapping.
    assert GitHubPullRequest.objects.filter(mapping=board.mapping, number=9).exists()
    assert GitHubIssueLink.objects.filter(issue=board.issue).exists()
    assert not GitHubRepositoryMapping.objects.filter(repository_id=555).exists()


def test_backfill_endpoint_requires_workspace_admin(board, session_client):
    with patch("plane.app.github_delivery.api.backfill_github_workspace.delay") as task:
        response = session_client.post(root(board) + "backfill/")
        assert response.status_code == 202
        task.assert_called_once_with(str(board.workspace.id))
    WorkspaceMember.objects.filter(workspace=board.workspace, member=board.user).update(role=15)
    assert session_client.post(root(board) + "backfill/").status_code == 403
    assert APIClient().post(root(board) + "backfill/").status_code in (401, 403)


# ---------------------------------------------------------------------------
# Feature 3: webhook health
# ---------------------------------------------------------------------------


def delivery(board, repository_id=789, status="processed", event="pull_request", when=None):
    row = GitHubWebhookDelivery.objects.create(
        id=uuid4(),
        connection=board.connection,
        app=board.app,
        host="github.com",
        installation_id=456,
        repository_id=repository_id,
        event=event,
        body_hash="0" * 64,
        status=status,
    )
    # received_at is auto_now_add; pin it through the queryset instead.
    GitHubWebhookDelivery.objects.filter(id=row.id).update(received_at=when or timezone.now())
    row.refresh_from_db()
    return row


def test_health_endpoint_shape_and_permissions(board, session_client):
    older = delivery(board, event="push", when=timezone.now() - timedelta(hours=2))
    delivery(board, status="failed", event="release", when=timezone.now() - timedelta(minutes=90))
    latest = delivery(board, event="pull_request", when=timezone.now() - timedelta(hours=1))
    delivery(board, repository_id=555, status="failed", event="push", when=timezone.now() - timedelta(minutes=30))
    response = session_client.get(root(board) + "health/")
    assert response.status_code == 200
    connections = response.data["connections"]
    assert len(connections) == 1
    connection = connections[0]
    assert connection["id"] == str(board.connection.id)
    assert connection["account"] == "team"
    assert connection["host"] == "github.com"
    assert connection["host_display"] == "GitHub"
    assert connection["active"] is True
    repos = {repo["mapping_id"]: repo for repo in connection["repos"]}
    assert set(repos) == {str(board.mapping.id)}
    repo = repos[str(board.mapping.id)]
    assert repo["full_name"] == "team/repo"
    assert repo["active"] is True and repo["auto"] is False
    assert repo["sync_status"] == board.mapping.sync_status
    assert repo["sync_error"] == ""
    assert repo["last_delivery_at"] == latest.received_at.isoformat()
    assert repo["last_event"] == "pull_request"
    assert repo["last_delivery_at"] > older.received_at.isoformat()
    assert repo["failed_deliveries"] == 1
    # A suspended mapping still reports; non-admins may not look.
    board.mapping.is_active = False
    board.mapping.sync_status = "failed"
    board.mapping.sync_error = "GitHub could not sync this repository."
    board.mapping.save(update_fields=["is_active", "sync_status", "sync_error"])
    response = session_client.get(root(board) + "health/")
    repo = response.data["connections"][0]["repos"][0]
    assert repo["active"] is False and repo["sync_status"] == "failed"
    assert repo["sync_error"] == "GitHub could not sync this repository."
    assert repo["last_synced_at"] is None
    WorkspaceMember.objects.filter(workspace=board.workspace, member=board.user).update(role=15)
    assert session_client.get(root(board) + "health/").status_code == 403
    assert APIClient().get(root(board) + "health/").status_code in (401, 403)


def test_branch_rule_moves_and_assigns_on_matching_merge(board, boundaries):
    board.issue.assignees.set([])
    add_rule(board, base_branch="main", state=board.done, assignee=board.user)
    add_rule(board, state=board.todo, base_branch="test")  # wrong branch: must not fire
    services.upsert_pull_request(board.mapping, merged_payload(board, number=9, base="test", updated_at="2026-09-25T12:30:00Z"))
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.todo.id and list(board.issue.assignees.values_list("id", flat=True)) == []
    # Merging into the rule's branch moves AND assigns in one step.
    boundaries.publish.reset_mock()
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=10, base="main", updated_at="2026-09-25T13:00:00Z", title=f"Also DEV-{board.issue.sequence_id}"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.done.id
    assert list(board.issue.assignees.values_list("id", flat=True)) == [board.user.id]
    assert boundaries.publish.called


def test_branch_rule_require_all_merged_toggle_and_precedence(board, boundaries):
    in_progress = State.objects.create(name="In Progress", group="started", color="#0055aa", project=board.project)
    # Catch-all runs first, the specific branch rule wins the conflict.
    add_rule(board, state=in_progress)
    add_rule(board, base_branch="release", state=board.done, require_all_merged=False)
    services.upsert_pull_request(board.mapping, pr_payload(board, number=8))  # still open
    # The release rule ignores the open pull request; it also wins over the catch-all.
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=9, base="release", updated_at="2026-09-25T13:00:00Z"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.done.id
    # A merge on a branch with no specific rule leaves the catch-all in charge,
    # and the catch-all still waits for every linked pull request to merge.
    board.issue.state = board.todo
    board.issue.save(update_fields=["state", "updated_at"])
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=11, base="chore", updated_at="2026-09-25T14:00:00Z", title=f"Chore DEV-{board.issue.sequence_id}"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == board.todo.id
    # Once the open pull request merges too, the catch-all completes the work.
    services.upsert_pull_request(
        board.mapping,
        merged_payload(board, number=8, base="chore", updated_at="2026-09-25T14:30:00Z"),
    )
    board.issue.refresh_from_db()
    assert board.issue.state_id == in_progress.id
