# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import include, path
from rest_framework.test import APIClient

import plane.urls as _plane_urls
from plane.db.models import Issue, Project, ProjectMember, State, User, Workspace, WorkspaceMember
from plane.db.models.asana_sync import (
    AsanaCommentLink,
    AsanaConnection,
    AsanaProjectSync,
    AsanaSyncLog,
    AsanaTaskLink,
)
from plane.db.models.github_delivery import (
    GitHubCheckRun,
    GitHubCommit,
    GitHubCommitIssueLink,
    GitHubConnection,
    GitHubIssueLink,
    GitHubPullRequest,
    GitHubPullRequestReview,
    GitHubRepositoryMapping,
)
from plane.db.models.slack_delivery import SlackChannelMapping, SlackConnection, SlackIssueLink, SlackMessage

pytestmark = [
    pytest.mark.contract,
    pytest.mark.django_db(transaction=True),
    pytest.mark.urls("plane.tests.contract.app.test_linked_activity"),
]

# The linked-activity path is mounted by the coordinator in plane/app/urls/__init__.py
# (see LANE-NOTES.md at the worktree root). Until that lands, route it locally so the
# contract is testable standalone: this module doubles as the ROOT_URLCONF.
urlpatterns = list(_plane_urls.urlpatterns) + [
    path("api/", include("plane.app.linked_activity.urls")),
]

EVENT_KEYS = {"id", "source", "type", "title", "url", "actor", "timestamp", "meta"}


def at(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


@pytest.fixture(autouse=True)
def boundaries():
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def setup(create_user):
    workspace = Workspace.objects.create(name="Timeline", slug="timeline-ws", owner=create_user)
    WorkspaceMember.objects.create(workspace=workspace, member=create_user, role=20, is_active=True)
    project = Project.objects.create(name="Timeline", identifier="TL", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    state = State.objects.create(
        name="Todo", group="unstarted", color="#555555", project=project, sequence=15000, default=True
    )
    issue = Issue.objects.create(name="Ship the thing", project=project, state=state)

    # --- GitHub: merged PR #42, one check, one review, one commit ------------
    gh_connection = GitHubConnection.objects.create(
        workspace=workspace,
        app=None,
        host="github.com",
        installation_id=456,
        account_login="team",
        github_user_id=7,
        connected_by=create_user,
        authorized_repository_ids=[789],
    )
    gh_mapping = GitHubRepositoryMapping.objects.create(
        connection=gh_connection, project=project, repository_id=789, full_name="team/repo"
    )
    pull_request = GitHubPullRequest.objects.create(
        mapping=gh_mapping,
        github_id=4200,
        number=42,
        title="fix asana cursor",
        state="merged",
        merged_at=at(2026, 1, 10, 10, 0),
        remote_created_at=at(2026, 1, 9, 9, 0),
        remote_closed_at=at(2026, 1, 10, 10, 0),
        base_ref="main",
    )
    GitHubIssueLink.objects.create(issue=issue, pull_request=pull_request)
    check_run = GitHubCheckRun.objects.create(
        pull_request=pull_request,
        check_run_id=9001,
        name="ci",
        conclusion="success",
        status="completed",
        completed_at=at(2026, 1, 9, 9, 30),
    )
    review = GitHubPullRequestReview.objects.create(
        pull_request=pull_request, review_id=5001, user_login="octocat", state="APPROVED", submitted_at=at(2026, 1, 9, 9, 15)
    )
    commit = GitHubCommit.objects.create(
        mapping=gh_mapping,
        sha="a" * 40,
        message="fix asana cursor\n\nlonger body",
        author_name="Ada Lovelace",
        committed_at=at(2026, 1, 8, 8, 0),
    )
    GitHubCommitIssueLink.objects.create(issue=issue, commit=commit)

    # --- Asana: task link, comment link, one ok and one error sync log -------
    asana_connection = AsanaConnection.objects.create(
        workspace=workspace,
        name="Main",
        pat_encrypted="encrypted-token",
        asana_workspace_gid="111",
        asana_workspace_name="Org",
    )
    asana_sync = AsanaProjectSync.objects.create(
        project=project,
        workspace=workspace,
        connection=asana_connection,
        asana_project_gid="222",
        asana_project_name="Delivery",
    )
    task_link = AsanaTaskLink.objects.create(
        project=project, workspace=workspace, sync=asana_sync, issue=issue, asana_task_gid="333"
    )
    comment_link = AsanaCommentLink.objects.create(
        project=project, workspace=workspace, task_link=task_link, asana_story_gid="444", direction="pull"
    )
    sync_log_ok = AsanaSyncLog.objects.create(
        project=project,
        workspace=workspace,
        sync=asana_sync,
        issue=issue,
        direction="pull",
        entity_type="task",
        entity_gid="333",
        status="success",
        message="Pulled task",
    )
    sync_log_error = AsanaSyncLog.objects.create(
        project=project,
        workspace=workspace,
        sync=asana_sync,
        issue=issue,
        direction="pull",
        entity_type="task",
        entity_gid="333",
        status="error",
        message="Rate limited",
    )

    # --- Slack: linked thread plus one reply ---------------------------------
    slack_connection = SlackConnection.objects.create(
        workspace=workspace,
        team_id="T0TL",
        team_name="Team",
        team_domain="team",
        slack_user_id="U0AUTHOR",
        bot_user_id="U0BOT001",
        connected_by=create_user,
    )
    slack_mapping = SlackChannelMapping.objects.create(
        connection=slack_connection, project=project, channel_id="C0TL", channel_name="general"
    )
    thread_parent = SlackMessage.objects.create(
        mapping=slack_mapping,
        ts="1768200000.000100",
        user_id="U100",
        thread_ts="1768200000.000100",
        text="Look at this issue",
        posted_at=at(2026, 1, 12, 12, 0),
    )
    thread_reply = SlackMessage.objects.create(
        mapping=slack_mapping,
        ts="1768200300.000100",
        user_id="U101",
        thread_ts="1768200000.000100",
        text="On it, picking it up now",
        posted_at=at(2026, 1, 12, 12, 5),
    )
    slack_link = SlackIssueLink.objects.create(issue=issue, message=thread_parent)

    # Pin the Asana created_at values (auto_now_add) so the merged ordering is
    # deterministic against the fixed GitHub and Slack timestamps above.
    AsanaTaskLink.objects.filter(pk=task_link.pk).update(created_at=at(2026, 1, 6, 9, 0))
    AsanaCommentLink.objects.filter(pk=comment_link.pk).update(created_at=at(2026, 1, 6, 9, 30))
    AsanaSyncLog.objects.filter(pk=sync_log_ok.pk).update(created_at=at(2026, 1, 7, 10, 0))
    AsanaSyncLog.objects.filter(pk=sync_log_error.pk).update(created_at=at(2026, 1, 7, 10, 30))

    return SimpleNamespace(
        workspace=workspace,
        project=project,
        issue=issue,
        user=create_user,
        gh_connection=gh_connection,
        gh_mapping=gh_mapping,
        pull_request=pull_request,
        check_run=check_run,
        review=review,
        commit=commit,
        asana_sync=asana_sync,
        task_link=task_link,
        comment_link=comment_link,
        sync_log_ok=sync_log_ok,
        sync_log_error=sync_log_error,
        slack_connection=slack_connection,
        slack_mapping=slack_mapping,
        thread_parent=thread_parent,
        thread_reply=thread_reply,
        slack_link=slack_link,
    )


def url(setup, **params):
    base = f"/api/workspaces/{setup.workspace.slug}/projects/{setup.project.id}/issues/{setup.issue.id}/linked-activity/"
    if params:
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return f"{base}?{query}"
    return base


@pytest.fixture
def api(setup, create_user):
    client = APIClient()
    client.force_authenticate(user=create_user)
    return client


def test_ordering_shape_and_count(setup, api):
    response = api.get(url(setup))
    assert response.status_code == 200
    body = response.json()
    # Every source contributes: 5 github (opened, merged, check, review, commit),
    # 4 asana (task, comment, ok log, error log), 2 slack (thread, reply).
    assert body["count"] == 11
    assert len(body["results"]) == body["count"]
    assert body["offset"] == 0
    assert body["limit"] == 50

    results = body["results"]
    assert results == sorted(results, key=lambda row: row["timestamp"], reverse=True)

    # Newest first across all three sources.
    assert [row["type"] for row in results] == [
        "slack_message",  # 2026-01-12 12:05
        "thread_linked",  # 2026-01-12 12:00
        "pr_merged",  # 2026-01-10 10:00
        "check_completed",  # 2026-01-09 09:30
        "review",  # 2026-01-09 09:15
        "pr_opened",  # 2026-01-09 09:00
        "commit",  # 2026-01-08 08:00
        "sync_error",  # 2026-01-07 10:30
        "sync_ok",  # 2026-01-07 10:00
        "comment_synced",  # 2026-01-06 09:30
        "task_linked",  # 2026-01-06 09:00
    ]

    for row in results:
        assert set(row) == EVENT_KEYS
        assert row["id"].startswith(f"{row['source']}-")
        assert row["url"] is None or row["url"].startswith("https://")

    types_by_source = {row["source"]: {r["type"] for r in results if r["source"] == row["source"]} for row in results}
    assert types_by_source["github"] == {"pr_opened", "pr_merged", "check_completed", "review", "commit"}
    assert types_by_source["asana"] == {"task_linked", "comment_synced", "sync_ok", "sync_error"}
    assert types_by_source["slack"] == {"thread_linked", "slack_message"}

    newest = results[0]
    assert newest["type"] == "slack_message"
    assert newest["title"] == "On it, picking it up now"
    assert newest["actor"] == "U101"
    assert newest["url"] == "https://team.slack.com/archives/C0TL/p1768200300000100"

    merged = next(row for row in results if row["type"] == "pr_merged")
    assert merged["timestamp"] == "2026-01-10T10:00:00+00:00"
    assert merged["title"] == "PR #42 merged: fix asana cursor"
    assert merged["meta"]["repository"] == "team/repo"

    reviewed = next(row for row in results if row["type"] == "review")
    assert reviewed["actor"] == "octocat"
    assert reviewed["title"] == "Review approved on PR #42 by octocat"

    committed = next(row for row in results if row["type"] == "commit")
    assert committed["title"].startswith(f"Commit {'a' * 7}: fix asana cursor")
    assert committed["url"] == f"https://github.com/team/repo/commit/{'a' * 40}"

    errors = next(row for row in results if row["type"] == "sync_error")
    assert "Rate limited" in errors["title"]


def test_source_filter(setup, api):
    response = api.get(url(setup, source="github"))
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 5
    assert {row["source"] for row in body["results"]} == {"github"}

    response = api.get(url(setup, source="asana"))
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4
    assert {row["source"] for row in body["results"]} == {"asana"}


def test_offset_and_limit_slicing(setup, api):
    full = api.get(url(setup)).json()["results"]
    response = api.get(url(setup, offset=1, limit=2))
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(full)
    assert body["offset"] == 1
    assert body["limit"] == 2
    assert len(body["results"]) == 2
    assert body["results"] == full[1:3]


def test_non_member_forbidden(setup):
    outsider = User.objects.create(email="outsider@timeline.test", username="outsider", password="x")
    WorkspaceMember.objects.create(workspace=setup.workspace, member=outsider, role=5, is_active=True)
    client = APIClient()
    client.force_authenticate(user=outsider)
    response = client.get(url(setup))
    assert response.status_code == 403

    stranger = User.objects.create(email="stranger@timeline.test", username="stranger", password="x")
    client = APIClient()
    client.force_authenticate(user=stranger)
    assert client.get(url(setup)).status_code == 403


def test_null_timestamp_fields_still_succeed(setup, api):
    """A commit with a null committed_at must not break the feed."""
    broken = GitHubCommit.objects.create(
        mapping=setup.gh_mapping,
        sha="b" * 40,
        message="no timestamp commit",
        committed_at=None,
    )
    GitHubCommitIssueLink.objects.create(issue=setup.issue, commit=broken)
    response = api.get(url(setup))
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 12
    assert all(row["timestamp"] for row in body["results"])
    assert any(row["id"] == f"github-commit-{broken.id}" for row in body["results"])
