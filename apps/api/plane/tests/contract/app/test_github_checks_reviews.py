# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember, State, User, WorkspaceMember
from plane.db.models.github_delivery import (
    GitHubApp,
    GitHubCheckRun,
    GitHubCommit,
    GitHubCommitIssueLink,
    GitHubConnection,
    GitHubPullRequest,
    GitHubPullRequestReview,
    GitHubRepositoryMapping,
    GitHubWebhookDelivery,
)
from plane.app.github_delivery import services
from plane.app.github_delivery.crypto import encrypt_secret

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]

WEBHOOK_SECRET = "webhook-test-secret"


@pytest.fixture(autouse=True)
def boundaries(settings):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.GITHUB_DELIVERY = {"BASE_URL": "http://localhost:3002"}
    boundaries.pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def board(workspace, create_user):
    project = Project.objects.create(name="Development", identifier="DEV", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    state = State.objects.create(name="Todo", group="unstarted", color="#555555", project=project)
    issue = Issue.objects.create(name="Build feature", project=project, state=state)
    app = GitHubApp.objects.create(
        host="github.com",
        app_id=123,
        slug="board-test",
        client_id="Iv.test",
        client_secret=encrypt_secret("test-secret"),
        private_key=encrypt_secret(boundaries.pem),
        webhook_secret=encrypt_secret(WEBHOOK_SECRET),
    )
    connection = GitHubConnection.objects.create(
        workspace=workspace,
        app=app,
        host="github.com",
        installation_id=456,
        account_login="team",
        github_user_id=7,
        connected_by=create_user,
        authorized_repository_ids=[789],
    )
    mapping = GitHubRepositoryMapping.objects.create(
        connection=connection, project=project, repository_id=789, full_name="team/repo", is_private=True
    )
    return SimpleNamespace(
        project=project,
        issue=issue,
        connection=connection,
        mapping=mapping,
        workspace=workspace,
        user=create_user,
        app=app,
    )


def issue_url(board):
    project = f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/"
    return project + f"issues/{board.issue.id}/github-pull-requests/"


def pr_payload(board, **overrides):
    return {
        "id": 900,
        "number": 9,
        "title": f"Build DEV-{board.issue.sequence_id}",
        "body": "Private evidence",
        "base": {"repo": {"id": 789}},
        "head": {"ref": "feature/DEV-999"},
        "state": "open",
        "updated_at": "2026-09-25T12:00:00Z",
        **overrides,
    }


def event(board, **overrides):
    return {
        "installation": {"id": 456},
        "repository": {"id": 789},
        "action": "opened",
        "pull_request": pr_payload(board),
        **overrides,
    }


def check_run_payload(board, check_run_id, status, conclusion=None, numbers=(9,)):
    return {
        "installation": {"id": 456},
        "repository": {"id": 789},
        "action": "completed" if status == "completed" else "created",
        "check_run": {
            "id": check_run_id,
            "name": "ci/build",
            "status": status,
            "conclusion": conclusion,
            "completed_at": "2026-09-25T12:05:00Z" if status == "completed" else None,
            "check_suite": {"pull_requests": [{"number": number} for number in numbers]},
        },
    }


def webhook(payload, delivery_id=None, event_name="pull_request", signature=None):
    raw = json.dumps(payload).encode()
    signature = signature or "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return APIClient().post(
        "/api/github-delivery/webhooks/",
        raw,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature,
        HTTP_X_GITHUB_DELIVERY=str(delivery_id or uuid4()),
        HTTP_X_GITHUB_EVENT=event_name,
    )


def deliver(payload, event_name):
    """Post a signed webhook and process it, like the queue worker would."""
    delivery_id = uuid4()
    response = webhook(payload, delivery_id, event_name)
    assert response.status_code == 202, getattr(response, "data", None)
    services.process_delivery(delivery_id)
    return delivery_id


def open_pr(board):
    deliver(event(board), "pull_request")
    return GitHubPullRequest.objects.get(mapping=board.mapping, number=9)


def checks_of(board):
    return services.list_issue_pull_requests(board.issue)[0]["checks"]


def manifest_value(content):
    import html
    import re

    return json.loads(html.unescape(re.search(r'name="manifest" value="([^"]*)"', content).group(1)))


def test_check_run_delivery_updates_checks_aggregate(board):
    pr = open_pr(board)
    # A pull request without check runs reports a zeroed aggregate.
    assert checks_of(board) == {"total": 0, "failed": 0, "pending": 0}
    # A passing check completes the suite.
    deliver(check_run_payload(board, 5001, "completed", "success"), "check_run")
    assert checks_of(board) == {"total": 1, "failed": 0, "pending": 0}
    # A failing check counts against the pull request.
    deliver(check_run_payload(board, 5002, "completed", "failure"), "check_run")
    assert checks_of(board) == {"total": 2, "failed": 1, "pending": 0}
    # A queued check is pending, and does not count as finished.
    deliver(check_run_payload(board, 5003, "queued"), "check_run")
    assert checks_of(board) == {"total": 2, "failed": 1, "pending": 1}
    assert GitHubCheckRun.objects.filter(pull_request=pr).count() == 3
    # The queued→completed transition updates the same row, keyed by check id.
    deliver(check_run_payload(board, 5003, "completed", "success"), "check_run")
    assert checks_of(board) == {"total": 3, "failed": 1, "pending": 0}
    assert GitHubCheckRun.objects.filter(pull_request=pr).count() == 3
    run = GitHubCheckRun.objects.get(pull_request=pr, check_run_id=5001)
    assert run.name == "ci/build" and run.conclusion == "success" and run.status == "completed"
    assert run.completed_at is not None
    # A skipped conclusion is not a failure.
    deliver(check_run_payload(board, 5004, "completed", "skipped"), "check_run")
    assert checks_of(board) == {"total": 4, "failed": 1, "pending": 0}
    # Check runs for numbers this mapping does not know are dropped silently.
    unknown = uuid4()
    response = webhook(check_run_payload(board, 5005, "completed", "success", numbers=(999,)), unknown, "check_run")
    assert response.status_code == 202
    services.process_delivery(unknown)
    assert GitHubWebhookDelivery.objects.get(id=unknown).status == "processed"
    assert GitHubCheckRun.objects.filter(check_run_id=5005).count() == 0
    assert checks_of(board) == {"total": 4, "failed": 1, "pending": 0}
    # Check runs cascade with their pull request.
    pr.delete()
    assert GitHubCheckRun.objects.count() == 0


def test_manifest_subscribes_new_apps_to_check_run_and_push(board, session_client):
    response = session_client.post(
        f"/api/workspaces/{board.workspace.slug}/github-delivery/connect/",
        {"account_type": "personal"},
        format="json",
    )
    assert response.status_code == 200, response.data
    start = urlsplit(response.data["url"])
    assert start.path == "/api/github-delivery/manifest/start/"
    page = session_client.get(start.path, {"state": parse_qs(start.query)["state"][0]})
    assert page.status_code == 200
    manifest = manifest_value(page.content.decode())
    assert "push" in manifest["default_events"]
    assert "check_run" in manifest["default_events"]
    assert manifest["default_events"] == ["pull_request", "pull_request_review", "release", "push", "check_run"]
    # every event needs a supporting permission or GitHub rejects the manifest
    # ("Default events are not supported by permissions: check_run")
    assert manifest["default_permissions"]["checks"] == "read"
    assert manifest["hook_attributes"]["url"] == "http://localhost:3002/api/github-delivery/webhooks/"


def test_review_delivery_stores_reviews_and_timeline_events(board):
    pr = open_pr(board)
    deliver(
        event(
            board,
            action="submitted",
            pull_request=pr_payload(board, updated_at="2026-09-25T12:30:00Z"),
            review={
                "id": 7001,
                "user": {"login": "devone"},
                "state": "changes_requested",
                "submitted_at": "2026-09-25T13:00:00Z",
            },
        ),
        "pull_request_review",
    )
    deliver(
        event(
            board,
            action="submitted",
            pull_request=pr_payload(board, updated_at="2026-09-25T12:30:00Z"),
            review={
                "id": 7002,
                "user": {"login": "reviewer"},
                "state": "approved",
                "submitted_at": "2026-09-25T14:00:00Z",
            },
        ),
        "pull_request_review",
    )
    pr.refresh_from_db()
    assert pr.review_state == "approved"  # the label keeps reporting the latest review
    reviews = GitHubPullRequestReview.objects.filter(pull_request=pr).order_by("review_id")
    assert [row.review_id for row in reviews] == [7001, 7002]
    assert reviews[0].user_login == "devone" and reviews[0].state == "changes_requested"
    assert reviews[0].submitted_at is not None
    timeline = services.list_issue_development(board.issue)["timeline"]
    review_events = [item for item in timeline if item["kind"] == "review"]
    assert len(review_events) == 2
    first = review_events[0]
    assert first["title"] == "Review Changes requested by devone"
    assert first["detail"] == "changes_requested"
    assert first["author"] == "devone"
    assert first["author_user_id"] is None  # logins carry no email; identity is email-based
    assert first["url"] == f"https://github.com/team/repo/pull/{pr.number}"
    assert first["repository"] == "team/repo"
    assert review_events[1]["title"] == "Review Approved by reviewer"
    # Unhumanized states pass through raw.
    deliver(
        event(
            board,
            action="submitted",
            pull_request=pr_payload(board, updated_at="2026-09-25T12:30:00Z"),
            review={
                "id": 7003,
                "user": {"login": "reviewer"},
                "state": "commented",
                "submitted_at": "2026-09-25T15:00:00Z",
            },
        ),
        "pull_request_review",
    )
    timeline = services.list_issue_development(board.issue)["timeline"]
    assert "Review commented by reviewer" in [item["title"] for item in timeline if item["kind"] == "review"]
    # Timeline stays oldest-first with reviews interleaved.
    times = [item["at"] for item in timeline if item["at"]]
    assert times == sorted(times)
    kinds = [item["kind"] for item in timeline]
    assert kinds.index("pr_opened") < kinds.index("review")
    # Reviews cascade with their pull request.
    pr.delete()
    assert GitHubPullRequestReview.objects.count() == 0


def test_commit_author_email_identity_from_push_and_search(board):
    member = User.objects.create(email="Dev.One@Example.test", username="member-dev")
    WorkspaceMember.objects.create(workspace=board.workspace, member=member, role=15, is_active=True)
    seq = board.issue.sequence_id
    push = {
        "installation": {"id": 456},
        "repository": {"id": 789},
        "ref": "refs/heads/main",
        "commits": [
            {
                "id": "a" * 40,
                "message": f"Fix DEV-{seq} properly",
                "author": {"name": "Dev One", "email": "dev.one@example.test", "username": "devone"},
                "timestamp": "2026-09-25T10:00:00Z",
            },
            {
                "id": "b" * 40,
                "message": f"Also DEV-{seq}",
                "author": {"name": "Guest", "email": "guest@example.test"},
                "timestamp": "2026-09-25T10:01:00Z",
            },
        ],
    }
    deliver(push, "push")
    assert GitHubCommit.objects.filter(sha="a" * 40).get().author_email == "dev.one@example.test"
    assert GitHubCommit.objects.filter(sha="b" * 40).get().author_email == "guest@example.test"
    commits = {item["sha"]: item for item in services.list_issue_development(board.issue)["commits"]}
    assert commits["a" * 40]["author_user_id"] == str(member.id)  # email matches a member, case-insensitively
    assert commits["b" * 40]["author_user_id"] is None  # a guest email maps to nobody
    # Search-shaped commits carry the email under commit.author.
    services.upsert_commit(
        board.mapping,
        {
            "sha": "c" * 40,
            "commit": {
                "message": f"chore: cleanup for DEV-{seq}",
                "author": {"name": "Dev One", "email": "Dev.One@example.test", "date": "2026-09-20T09:00:00Z"},
            },
            "author": {"login": "devone"},
        },
    )
    found = GitHubCommit.objects.get(sha="c" * 40)
    assert found.author_email == "Dev.One@example.test"
    assert services.commit_data(found)["author_user_id"] == str(member.id)
    assert GitHubCommitIssueLink.objects.filter(issue=board.issue, commit=found).exists()


def test_project_dev_status_summary(board, session_client):
    pr = open_pr(board)  # links to the board issue through the PR payload mention
    GitHubCheckRun.objects.create(pull_request=pr, check_run_id=6001, name="ci/build", status="completed", conclusion="failure")
    GitHubCheckRun.objects.create(pull_request=pr, check_run_id=6002, name="ci/lint", status="in_progress")
    base = f"/api/workspaces/{board.workspace.slug}/projects/{board.project.id}/github-delivery/dev-status/"
    response = session_client.get(base)
    assert response.status_code == 200, response.data
    entry = response.data["issues"][str(board.issue.id)]
    assert entry == {"open": 1, "merged": 0, "closed": 0, "failing": 1, "pending": 1, "commits": 0}
    GitHubCommitIssueLink.objects.create(
        issue=board.issue,
        commit=GitHubCommit.objects.create(mapping=board.mapping, sha="d" * 40, message="DEV work"),
    )
    entry = session_client.get(base).data["issues"][str(board.issue.id)]
    assert entry["commits"] == 1
    # Another project's tickets never leak into this project's summary.
    outsider = User.objects.create(email="out2@example.test", username="out2")
    APIClient().force_authenticate(outsider)
    assert APIClient().get(base).status_code in (401, 403, 404)
