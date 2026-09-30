# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import clear_url_caches
from django.utils import timezone
from rest_framework.test import APIClient

import plane.app.urls as app_urls
from plane.app.release_intelligence.provider import IntelligenceError
from plane.app.sync_health.api import EXPLAIN_INSTRUCTIONS
from plane.app.sync_health.urls import urlpatterns as sync_health_urlpatterns
from plane.db.models import Project, ProjectMember, User, Workspace, WorkspaceMember
from plane.db.models.ai_audit import AIActionAudit
from plane.db.models.asana_sync import AsanaConnection, AsanaProjectSync, AsanaSyncLog
from plane.db.models.github_delivery import (
    GitHubAutomationRule,
    GitHubConnection,
    GitHubPullRequest,
    GitHubRepositoryMapping,
    GitHubWebhookDelivery,
)
from plane.db.models.slack_delivery import (
    SlackChannelMapping,
    SlackConnection,
    SlackIssueLink,
    SlackMessage,
)

pytestmark = [pytest.mark.contract, pytest.mark.django_db(transaction=True)]


@pytest.fixture(scope="session", autouse=True)
def wire_sync_health_urls():
    """The production include lives in plane/app/urls/__init__.py (coordinator
    wiring); until it lands, append this lane's patterns to the already-loaded
    module and clear the resolver cache so contract tests can hit the route."""
    if "sync-health" not in {getattr(pattern, "name", None) for pattern in app_urls.urlpatterns}:
        app_urls.urlpatterns += list(sync_health_urlpatterns)
        clear_url_caches()
    yield


@pytest.fixture(autouse=True)
def boundaries():
    with patch("plane.bgtasks.workitem_realtime._schedule_publish"), patch("celery.app.task.Task.apply_async"):
        yield


@pytest.fixture
def setup(create_user):
    workspace = Workspace.objects.create(name="Sync Health", slug="sync-health", owner=create_user)
    WorkspaceMember.objects.create(workspace=workspace, member=create_user, role=20, is_active=True)
    member = User.objects.create(email="member@sync-health.test", username="member", password="x")
    WorkspaceMember.objects.create(workspace=workspace, member=member, role=5, is_active=True)
    project = Project.objects.create(name="Health", identifier="HLTH", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, role=20, is_active=True)
    return SimpleNamespace(workspace=workspace, admin=create_user, member=member, project=project)


def make_asana_sync(setup, *, last_synced_at=None, name="Main", gid="999"):
    connection = AsanaConnection.objects.create(
        workspace=setup.workspace,
        name=name,
        pat_encrypted="encrypted-token",
        asana_workspace_gid="42",
        asana_workspace_name="Asana Org",
    )
    sync = AsanaProjectSync.objects.create(
        project=setup.project,
        connection=connection,
        asana_project_gid=gid,
        asana_project_name="Asana Project",
        last_synced_at=last_synced_at,
    )
    return connection, sync


def make_github_mapping(setup, *, repo="acme/plane"):
    connection = GitHubConnection.objects.create(
        workspace=setup.workspace,
        installation_id=int(uuid.uuid4().hex[:12], 16),
        account_login="acme",
        github_user_id=int(uuid.uuid4().hex[:8], 16),
    )
    mapping = GitHubRepositoryMapping.objects.create(
        connection=connection,
        project=setup.project,
        repository_id=int(uuid.uuid4().hex[:8], 16),
        full_name=repo,
    )
    return connection, mapping


def make_slack_mapping(setup, *, sync_status="synced", last_synced_at=None, active=True, channel="general"):
    connection = SlackConnection.objects.create(
        workspace=setup.workspace,
        team_id=uuid.uuid4().hex[:10],
        team_name="Team",
        team_domain="team",
        slack_user_id="U0AUTHOR",
        bot_user_id="U0BOT001",
    )
    mapping = SlackChannelMapping.objects.create(
        connection=connection,
        project=setup.project,
        channel_id=uuid.uuid4().hex[:10],
        channel_name=channel,
        sync_status=sync_status,
        last_synced_at=last_synced_at,
        is_active=active,
    )
    return connection, mapping


def asana_log(sync, *, status, message):
    # AsanaSyncLog is a ProjectBaseModel: its save() needs the project side.
    return AsanaSyncLog.objects.create(
        project=sync.project, sync=sync, entity_type="task", status=status, message=message
    )


def webhook_delivery(connection, mapping, *, status, event="pull_request"):
    # GitHubWebhookDelivery's id is GitHub's X-GitHub-Delivery GUID (no default).
    return GitHubWebhookDelivery.objects.create(
        id=uuid.uuid4(),
        connection=connection,
        installation_id=connection.installation_id,
        repository_id=mapping.repository_id,
        event=event,
        body_hash=uuid.uuid4().hex,
        status=status,
        error="boom" if status == "failed" else "",
    )


def get_health(user, workspace, params=None):
    client = APIClient()
    client.force_authenticate(user=user)
    return client.get(f"/api/workspaces/{workspace.slug}/integrations/sync-health/", params)


# --- Asana grading ---


def test_asana_fresh_cursor_no_errors_is_green(setup):
    _, sync = make_asana_sync(setup, last_synced_at=timezone.now())
    asana_log(sync, status="success", message="ok")

    response = get_health(setup.admin, setup.workspace)

    assert response.status_code == 200
    item = response.data["asana"][0]
    assert item["health"] == "green"
    assert item["cursor_age_minutes"] is not None and item["cursor_age_minutes"] < 3
    assert item["errors_24h"] == 0
    assert item["last_log"]["level"] == "success"
    assert response.data["overall"]["asana"] == "green"
    assert response.data["overall"]["github"] == "none"
    assert response.data["overall"]["slack"] == "none"
    assert response.data["computed_at"]


def test_asana_cursor_25h_old_is_red(setup):
    make_asana_sync(setup, last_synced_at=timezone.now() - timedelta(hours=25))

    item = get_health(setup.admin, setup.workspace).data["asana"][0]

    assert item["health"] == "red"
    assert item["cursor_age_minutes"] >= 25 * 60


def test_asana_single_error_is_amber(setup):
    _, sync = make_asana_sync(setup, last_synced_at=timezone.now())
    asana_log(sync, status="error", message="remote 403")

    item = get_health(setup.admin, setup.workspace).data["asana"][0]

    assert item["health"] == "amber"
    assert item["errors_24h"] == 1
    assert item["last_log"]["level"] == "error"
    assert item["last_log"]["message"] == "remote 403"


def test_asana_three_warnings_are_amber(setup):
    _, sync = make_asana_sync(setup, last_synced_at=timezone.now())
    for index in range(3):
        asana_log(sync, status="skipped", message=f"skip {index}")

    item = get_health(setup.admin, setup.workspace).data["asana"][0]

    assert item["health"] == "amber"
    assert item["warnings_24h"] == 3
    assert item["errors_24h"] == 0


# --- GitHub grading ---


def test_github_never_delivered_is_red(setup):
    make_github_mapping(setup)

    item = get_health(setup.admin, setup.workspace).data["github"][0]

    assert item["health"] == "red"
    assert item["last_delivery_at"] is None
    assert item["delivery_errors_24h"] == 0


def test_github_single_failed_delivery_is_amber(setup):
    connection, mapping = make_github_mapping(setup)
    webhook_delivery(connection, mapping, status="failed")

    item = get_health(setup.admin, setup.workspace).data["github"][0]

    assert item["health"] == "amber"
    assert item["delivery_errors_24h"] == 1


def test_github_five_failed_deliveries_are_red(setup):
    connection, mapping = make_github_mapping(setup)
    for _ in range(5):
        webhook_delivery(connection, mapping, status="failed", event="push")

    item = get_health(setup.admin, setup.workspace).data["github"][0]

    assert item["health"] == "red"
    assert item["delivery_errors_24h"] == 5


def test_github_healthy_mapping_counts_are_green(setup):
    connection, mapping = make_github_mapping(setup)
    webhook_delivery(connection, mapping, status="processed")
    now = timezone.now()
    GitHubPullRequest.objects.create(
        mapping=mapping, github_id=1, number=1, title="Open PR", remote_created_at=now, remote_closed_at=None
    )
    GitHubPullRequest.objects.create(
        mapping=mapping, github_id=2, number=2, title="Merged PR", remote_created_at=now, remote_closed_at=now
    )
    GitHubAutomationRule.objects.create(project=setup.project)

    item = get_health(setup.admin, setup.workspace).data["github"][0]

    assert item["health"] == "green"
    assert item["last_delivery_at"] is not None
    assert item["open_prs_tracked"] == 1
    assert item["last_pr_at"] is not None
    assert item["automation_rules"] == 1


# --- Slack grading ---


def test_slack_error_status_is_red(setup):
    make_slack_mapping(setup, sync_status="error")

    item = get_health(setup.admin, setup.workspace).data["slack"][0]

    assert item["health"] == "red"
    assert item["active"] is True


def test_slack_stale_cursor_is_amber(setup):
    make_slack_mapping(setup, sync_status="synced", last_synced_at=timezone.now() - timedelta(hours=25))

    item = get_health(setup.admin, setup.workspace).data["slack"][0]

    assert item["health"] == "amber"


def test_slack_healthy_counts_are_green(setup):
    connection, mapping = make_slack_mapping(
        setup, sync_status="synced", last_synced_at=timezone.now() - timedelta(minutes=5)
    )
    message = SlackMessage.objects.create(
        mapping=mapping, ts=f"{timezone.now().timestamp():.6f}", text="hello", posted_at=timezone.now()
    )
    from plane.db.models import Issue

    issue = Issue.objects.create(name="Tracked issue", project=setup.project)
    SlackIssueLink.objects.create(issue=issue, message=message)

    item = get_health(setup.admin, setup.workspace).data["slack"][0]

    assert item["health"] == "green"
    assert item["messages_24h"] == 1
    assert item["issue_links"] == 1


# --- overall / permissions ---


def test_overall_none_when_workspace_has_no_integrations(create_user):
    admin = create_user
    workspace = Workspace.objects.create(name="Empty", slug="empty-sync-health", owner=admin)
    WorkspaceMember.objects.create(workspace=workspace, member=admin, role=20, is_active=True)

    response = get_health(admin, workspace)

    assert response.status_code == 200
    assert response.data["overall"] == {"asana": "none", "github": "none", "slack": "none"}
    assert response.data["asana"] == []
    assert response.data["github"] == []
    assert response.data["slack"] == []


def test_non_admin_gets_403(setup):
    response = get_health(setup.member, setup.workspace)

    assert response.status_code == 403


# --- AI drift explainer ---


def test_explain_success_records_audit(setup):
    _, sync = make_asana_sync(setup, last_synced_at=timezone.now())
    asana_log(sync, status="error", message="remote 500")
    report = "- Asana is falling behind...\n- GitHub is healthy...\n- Retry the Asana sync now."
    captured = {}

    def fake_generate_text(project, sources, instructions, review=False):
        captured["project"] = project
        captured["sources"] = sources
        captured["instructions"] = instructions
        return {"text": report, "model": "test-model"}

    with patch("plane.app.sync_health.api.generate_text", side_effect=fake_generate_text):
        response = get_health(setup.admin, setup.workspace, {"explain": "true"})

    assert response.status_code == 200
    assert response.data["explain"] == report
    assert response.data["explain_model"] == "test-model"
    assert "explain" not in response.data["asana"][0]
    assert captured["instructions"] == EXPLAIN_INSTRUCTIONS
    assert captured["project"].id == setup.project.id
    snapshot = captured["sources"][0]
    assert snapshot["id"] == "sync-health-snapshot"
    assert '"health"' in snapshot["content"]

    audit = AIActionAudit.objects.get(workspace=setup.workspace, action="integrations.sync_health_report")
    assert audit.status == AIActionAudit.Status.SUCCESS
    assert audit.model == "test-model"
    assert audit.output_excerpt == report


def test_explain_provider_error_still_returns_200(setup):
    make_slack_mapping(setup, sync_status="error")

    with patch(
        "plane.app.sync_health.api.generate_text",
        side_effect=IntelligenceError("Configure an AI API key and model in instance settings."),
    ):
        response = get_health(setup.admin, setup.workspace, {"explain": "true"})

    assert response.status_code == 200
    assert response.data["explain"] is None
    assert response.data["explain_error"] == "ai_unconfigured"
    assert response.data["slack"][0]["health"] == "red"

    audit = AIActionAudit.objects.get(workspace=setup.workspace, action="integrations.sync_health_report")
    assert audit.status == AIActionAudit.Status.ERROR
    assert "Configure an AI API key" in audit.error


def test_explain_without_any_integrations_is_unconfigured(setup):
    with patch("plane.app.sync_health.api.generate_text") as mock_generate:
        response = get_health(setup.admin, setup.workspace, {"explain": "true"})

    assert response.status_code == 200
    assert response.data["explain"] is None
    assert response.data["explain_error"] == "ai_unconfigured"
    mock_generate.assert_not_called()
