# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Sync-health / drift explainer: one workspace-admin endpoint that grades
Asana, GitHub and Slack integration health per connection from the fork's
sidecar tables, with an optional LLM plain-language drift report.

Read-only over the integration tables; the only write is the best-effort
AI audit row. Grading thresholds (per section):

- Asana: red when the sync cursor is older than 24h or 3+ errors in 24h;
  amber when the cursor is older than 2h, or any error, or 3+ warnings
  (skipped/conflict log rows) in 24h; else green.
- GitHub: red when 5+ failed webhook deliveries in 24h or nothing was ever
  delivered; amber on any failed delivery in 24h; else green.
- Slack: red when an active channel mapping reports sync_status "error";
  amber when an active mapping's sync cursor is stale beyond 24h; else green.

A section with no configured connections grades "none" overall.
"""

import json
from datetime import timedelta

from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from plane.app.ai_ops.service import log_ai_action
from plane.app.release_intelligence.provider import IntelligenceError, generate_text
from plane.app.views.base import BaseAPIView
from plane.db.models import Project, Workspace, WorkspaceMember
from plane.db.models.ai_audit import AIActionAudit
from plane.db.models.asana_sync import (
    AsanaCommentLink,
    AsanaProjectSync,
    AsanaSyncLog,
    AsanaTaskLink,
)
from plane.db.models.github_delivery import (
    GitHubAutomationRule,
    GitHubPullRequest,
    GitHubRepositoryMapping,
    GitHubWebhookDelivery,
)
from plane.db.models.slack_delivery import (
    SlackChannelMapping,
    SlackIssueLink,
    SlackMessage,
)

EXPLAIN_INSTRUCTIONS = (
    "You are explaining integration sync health to a workspace admin. Given this JSON health snapshot, "
    "write a 3-bullet plain-language status: what is healthy, what needs attention (name the exact "
    "integration + symptom), and the single most useful next action. No markdown headers. Max 120 words."
)

# Evidence budget handed to the LLM (generate_text caps each source's content
# again, so an over-long snapshot degrades gracefully instead of erroring).
SOURCE_BUDGET = 20000

GREEN = "green"
AMBER = "amber"
RED = "red"
_GRADE_ORDER = {GREEN: 0, AMBER: 1, RED: 2}

ASANA_WARNING_STATUSES = ("skipped", "conflict")
GITHUB_FAILED_STATUS = "failed"
SLACK_ERROR_STATUS = "error"


def workspace_admin(user, slug):
    workspace = get_object_or_404(Workspace, slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=workspace, member=user, role=20, is_active=True).exists()
    ):
        raise PermissionDenied("Workspace administrators can view integration sync health.")
    return workspace


def _iso(value):
    return value.isoformat() if value else None


def _cursor_age_minutes(value, now):
    """Minutes since the sync cursor advanced; None when it never advanced."""
    if not value:
        return None
    return max(int((now - value).total_seconds() // 60), 0)


def _worst(grades):
    if not grades:
        return "none"
    return max(grades, key=lambda grade: _GRADE_ORDER[grade])


def _mask_gid(value):
    """Show only the tail of a remote gid in admin-facing payloads."""
    if not value:
        return ""
    return "•" * max(len(value) - 4, 0) + value[-4:]


def _project_ref(project_id, project_name):
    return {"id": str(project_id), "name": project_name}


def asana_section(workspace, now):
    """Grade every Asana project sync under this workspace's connections."""
    window = now - timedelta(hours=24)
    syncs = (
        AsanaProjectSync.objects.filter(connection__workspace=workspace)
        .select_related("project", "connection")
        .order_by("-created_at")
    )
    items = []
    for sync in syncs:
        logs = AsanaSyncLog.objects.filter(sync=sync)
        errors_24h = logs.filter(created_at__gte=window, status="error").count()
        warnings_24h = logs.filter(created_at__gte=window, status__in=ASANA_WARNING_STATUSES).count()
        last_log = logs.order_by("-created_at").values("status", "message", "created_at").first()
        cursor_age_minutes = _cursor_age_minutes(sync.last_synced_at, now)

        if (cursor_age_minutes is not None and cursor_age_minutes > 24 * 60) or errors_24h >= 3:
            health = RED
        elif (
            (cursor_age_minutes is not None and cursor_age_minutes > 2 * 60)
            or errors_24h >= 1
            or warnings_24h >= 3
        ):
            health = AMBER
        else:
            health = GREEN

        items.append(
            {
                "sync_id": str(sync.id),
                "project": _project_ref(sync.project_id, sync.project.name),
                "connection_name": sync.connection.name,
                "workspace_gid_masked": _mask_gid(sync.connection.asana_workspace_gid),
                "direction": sync.direction,
                "active": sync.is_active,
                "initial_sync_done": sync.initial_sync_done,
                "last_synced_at": _iso(sync.last_synced_at),
                "cursor_age_minutes": cursor_age_minutes,
                "last_log": (
                    {
                        "level": last_log["status"],
                        "message": (last_log["message"] or "")[:200],
                        "created_at": _iso(last_log["created_at"]),
                    }
                    if last_log
                    else None
                ),
                "errors_24h": errors_24h,
                "warnings_24h": warnings_24h,
                "task_links": AsanaTaskLink.objects.filter(sync=sync).count(),
                "comment_links": AsanaCommentLink.objects.filter(task_link__sync=sync).count(),
                "health": health,
            }
        )
    return items


def github_section(workspace, now):
    """Grade every GitHub repository mapping under this workspace's connections."""
    window = now - timedelta(hours=24)
    mappings = (
        GitHubRepositoryMapping.objects.filter(connection__workspace=workspace)
        .select_related("project", "connection")
        .order_by("-created_at")
    )
    items = []
    for mapping in mappings:
        deliveries = GitHubWebhookDelivery.objects.filter(
            connection_id=mapping.connection_id,
            repository_id=mapping.repository_id,
        )
        last_delivery_at = deliveries.aggregate(latest=Max("received_at"))["latest"]
        delivery_errors_24h = deliveries.filter(
            status=GITHUB_FAILED_STATUS,
            received_at__gte=window,
        ).count()
        tracked_prs = GitHubPullRequest.objects.filter(mapping=mapping)
        last_pr_at = tracked_prs.aggregate(latest=Max("remote_created_at"))["latest"]

        if delivery_errors_24h >= 5 or last_delivery_at is None:
            health = RED
        elif delivery_errors_24h >= 1:
            health = AMBER
        else:
            health = GREEN

        items.append(
            {
                "mapping_id": str(mapping.id),
                "repo": mapping.full_name,
                "project": _project_ref(mapping.project_id, mapping.project.name),
                "account": mapping.connection.account_login,
                "active": mapping.is_active,
                "sync_status": mapping.sync_status,
                "sync_error": mapping.sync_error,
                "last_delivery_at": _iso(last_delivery_at),
                "delivery_errors_24h": delivery_errors_24h,
                "automation_rules": GitHubAutomationRule.objects.filter(project=mapping.project).count(),
                "open_prs_tracked": tracked_prs.filter(remote_closed_at__isnull=True).count(),
                "last_pr_at": _iso(last_pr_at),
                "health": health,
            }
        )
    return items


def slack_section(workspace, now):
    """Grade every Slack channel mapping under this workspace's connections."""
    window = now - timedelta(hours=24)
    stale_before = now - timedelta(hours=24)
    mappings = (
        SlackChannelMapping.objects.filter(connection__workspace=workspace)
        .select_related("project", "connection")
        .order_by("-created_at")
    )
    items = []
    for mapping in mappings:
        if mapping.is_active and mapping.sync_status == SLACK_ERROR_STATUS:
            health = RED
        elif mapping.is_active and (mapping.last_synced_at is None or mapping.last_synced_at < stale_before):
            health = AMBER
        else:
            health = GREEN

        items.append(
            {
                "mapping_id": str(mapping.id),
                "channel": mapping.channel_name,
                "project": _project_ref(mapping.project_id, mapping.project.name),
                "team": mapping.connection.team_name,
                "active": mapping.is_active,
                "sync_status": mapping.sync_status,
                "sync_error": mapping.sync_error,
                "last_synced_at": _iso(mapping.last_synced_at),
                "issue_links": SlackIssueLink.objects.filter(message__mapping=mapping).count(),
                "messages_24h": SlackMessage.objects.filter(
                    mapping=mapping,
                    posted_at__gte=window,
                ).count(),
                "health": health,
            }
        )
    return items


def _first_mapped_project(workspace):
    """The LLM adapter needs a project context; use the first mapped project
    across integrations (Asana, then GitHub, then Slack)."""
    project_id = (
        AsanaProjectSync.objects.filter(connection__workspace=workspace)
        .values_list("project_id", flat=True)
        .first()
    )
    if project_id is None:
        project_id = (
            GitHubRepositoryMapping.objects.filter(connection__workspace=workspace)
            .values_list("project_id", flat=True)
            .first()
        )
    if project_id is None:
        project_id = (
            SlackChannelMapping.objects.filter(connection__workspace=workspace)
            .values_list("project_id", flat=True)
            .first()
        )
    if project_id is None:
        return None
    return Project.objects.filter(id=project_id).first()


class SyncHealthEndpoint(BaseAPIView):
    """GET workspaces/<slug>/integrations/sync-health/ — workspace admins only.

    Optional ?explain=true appends {"explain", "explain_model"} (or
    {"explain": None, "explain_error"}) from the LLM drift explainer.
    Explain failures never fail the endpoint.
    """

    def get(self, request, slug):
        workspace = workspace_admin(request.user, slug)
        now = timezone.now()
        asana = asana_section(workspace, now)
        github = github_section(workspace, now)
        slack = slack_section(workspace, now)
        payload = {
            "asana": asana,
            "github": github,
            "slack": slack,
            "overall": {
                "asana": _worst([item["health"] for item in asana]),
                "github": _worst([item["health"] for item in github]),
                "slack": _worst([item["health"] for item in slack]),
            },
            "computed_at": _iso(now),
        }
        response = dict(payload)
        if request.query_params.get("explain", "").lower() in ("true", "1"):
            response.update(self._explain(request, workspace, payload))
        return Response(response, status=status.HTTP_200_OK)

    def _explain(self, request, workspace, payload):
        project = _first_mapped_project(workspace)
        if project is None:
            # Nothing is mapped, so there is nothing to explain and no project
            # context for the LLM adapter.
            return {"explain": None, "explain_error": "ai_unconfigured"}

        snapshot = {key: value for key, value in payload.items() if not key.startswith("explain")}
        sources = [
            {
                "id": "sync-health-snapshot",
                "kind": "json",
                "title": "Integration sync health snapshot",
                "content": json.dumps(snapshot, default=str)[:SOURCE_BUDGET],
            }
        ]
        try:
            result = generate_text(project, sources, EXPLAIN_INSTRUCTIONS, review=False)
        except IntelligenceError as exc:
            log_ai_action(
                workspace=workspace,
                action="integrations.sync_health_report",
                project=project,
                actor=request.user,
                entity_type="workspace",
                entity_id=workspace.id,
                status=AIActionAudit.Status.ERROR,
                input_excerpt="",
                error=str(exc),
            )
            return {"explain": None, "explain_error": "ai_unconfigured"}
        except Exception as exc:  # provider/network outages must not fail the report
            log_ai_action(
                workspace=workspace,
                action="integrations.sync_health_report",
                project=project,
                actor=request.user,
                entity_type="workspace",
                entity_id=workspace.id,
                status=AIActionAudit.Status.ERROR,
                input_excerpt="",
                error=str(exc),
            )
            return {"explain": None, "explain_error": "ai_unavailable"}

        text = result.get("text") or ""
        model = result.get("model") or ""
        log_ai_action(
            workspace=workspace,
            action="integrations.sync_health_report",
            project=project,
            actor=request.user,
            entity_type="workspace",
            entity_id=workspace.id,
            model=model,
            input_excerpt="",
            output_excerpt=text,
        )
        return {"explain": text, "explain_model": model}
