# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Cross-platform linked-activity timeline (Rovo Teamwork Graph parity).

A read-only aggregation of everything the fork's integrations already know
about one work item: GitHub pull requests, checks, reviews and commits; Asana
task links, comment links and sync logs; Slack linked threads and messages.
No migrations — this only reads tables written by the github_delivery,
asana_sync and slack_delivery lanes.
"""

import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.db.models import Issue, Project, ProjectMember, Workspace, WorkspaceMember
from plane.db.models.asana_sync import AsanaCommentLink, AsanaSyncLog, AsanaTaskLink
from plane.db.models.github_delivery import (
    GitHubCheckRun,
    GitHubCommitIssueLink,
    GitHubIssueLink,
    GitHubPullRequest,
    GitHubPullRequestReview,
)
from plane.db.models.slack_delivery import SlackIssueLink, SlackMessage

logger = logging.getLogger(__name__)

# Per-source candidate cap before the merge and the offset/limit slice.
PER_SOURCE_CAP = 100
TITLE_LIMIT = 200
KNOWN_SOURCES = ("github", "asana", "slack")

PASSING_CONCLUSIONS = ("success", "skipped", "neutral")


def project_member(user, slug, project_id, *, admin=False):
    """Permission idiom shared with plane.app.slack_delivery.api."""
    project = get_object_or_404(Project, id=project_id, workspace__slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=project.workspace, member=user, is_active=True).exists()
    ):
        raise PermissionDenied()
    members = ProjectMember.objects.filter(project=project, member=user, is_active=True, role__gte=15)
    if not members.exists() or admin and not members.filter(role=20).exists():
        raise PermissionDenied("An active project membership is required.")
    return project


def truncate(text, limit=TITLE_LIMIT):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def first_line(text):
    text = (text or "").strip()
    return text.splitlines()[0].strip() if text else ""


def iso(value):
    try:
        return value.isoformat() if value else None
    except (AttributeError, ValueError):
        return None


def github_web_base(host):
    return "https://github.com" if not host or host == "github.com" else f"https://{host}"


def pull_request_url(pull_request):
    return (
        f"{github_web_base(pull_request.mapping.connection.host)}"
        f"/{pull_request.mapping.full_name}/pull/{pull_request.number}"
    )


def commit_url(commit):
    return (
        f"{github_web_base(commit.mapping.connection.host)}"
        f"/{commit.mapping.full_name}/commit/{commit.sha}"
    )


def asana_task_url(task_link):
    connection = task_link.sync.connection
    task_gid = task_link.asana_task_gid
    if connection.asana_workspace_gid:
        return f"https://app.asana.com/1/{connection.asana_workspace_gid}/task/{task_gid}"
    if task_link.sync.asana_project_gid:
        return f"https://app.asana.com/0/{task_link.sync.asana_project_gid}/{task_gid}"
    return None


def slack_message_url(message):
    domain = message.mapping.connection.team_domain
    if not domain:
        return None
    return f"https://{domain}.slack.com/archives/{message.mapping.channel_id}/p{message.ts.replace('.', '')}"


def make_event(event_id, source, event_type, title, url, actor, timestamp, meta=None):
    """One feed row. `timestamp` is a datetime here; the endpoint converts to ISO.

    `url` is only surfaced when it is a real https link.
    """
    return {
        "id": event_id,
        "source": source,
        "type": event_type,
        "title": truncate(title),
        "url": url if url and url.startswith("https://") else None,
        "actor": actor,
        "timestamp": timestamp,
        "meta": meta or {},
    }


def github_events(issue, project):
    """Events for pull requests, checks, reviews and commits linked to the issue."""
    events = []

    pull_request_ids = list(
        GitHubIssueLink.objects.filter(issue=issue, is_suppressed=False).values_list("pull_request_id", flat=True)
    )[:PER_SOURCE_CAP]
    pull_requests = (
        GitHubPullRequest.objects.filter(id__in=pull_request_ids)
        .select_related("mapping", "mapping__connection")
        .order_by("-remote_created_at", "-remote_updated_at", "number")[:PER_SOURCE_CAP]
    )
    pr_ids = []
    for pull_request in pull_requests:
        pr_ids.append(pull_request.id)
        try:
            base = {"number": pull_request.number, "repository": pull_request.mapping.full_name}
            url = pull_request_url(pull_request)
            opened_at = pull_request.remote_created_at or pull_request.remote_updated_at or pull_request.updated_at
            events.append(
                make_event(
                    f"github-pr_opened-{pull_request.id}",
                    "github",
                    "pr_opened",
                    f"PR #{pull_request.number} opened: {pull_request.title}",
                    url,
                    None,
                    opened_at,
                    base,
                )
            )
            if pull_request.merged_at:
                meta = dict(base)
                if pull_request.base_ref:
                    meta["base_ref"] = pull_request.base_ref
                events.append(
                    make_event(
                        f"github-pr_merged-{pull_request.id}",
                        "github",
                        "pr_merged",
                        f"PR #{pull_request.number} merged: {pull_request.title}",
                        url,
                        None,
                        pull_request.merged_at,
                        meta,
                    )
                )
            elif pull_request.state == "closed" and pull_request.remote_closed_at:
                events.append(
                    make_event(
                        f"github-pr_closed-{pull_request.id}",
                        "github",
                        "pr_closed",
                        f"PR #{pull_request.number} closed: {pull_request.title}",
                        url,
                        None,
                        pull_request.remote_closed_at,
                        base,
                    )
                )
        except Exception:
            logger.exception("linked-activity: failed to build pull request events for %s", pull_request.id)

    if pr_ids:
        checks = (
            GitHubCheckRun.objects.filter(pull_request_id__in=pr_ids)
            .select_related("pull_request", "pull_request__mapping", "pull_request__mapping__connection")
            .order_by("-completed_at", "-updated_at")[:PER_SOURCE_CAP]
        )
        for check in checks:
            try:
                pull_request = check.pull_request
                failed = bool(check.conclusion) and check.conclusion not in PASSING_CONCLUSIONS
                events.append(
                    make_event(
                        f"github-check_completed-{check.id}",
                        "github",
                        "check_completed",
                        f"Check {check.name} {check.conclusion or check.status}: PR #{pull_request.number}",
                        pull_request_url(pull_request),
                        None,
                        check.completed_at or check.updated_at,
                        {
                            "number": pull_request.number,
                            "name": check.name,
                            "conclusion": check.conclusion,
                            "status": check.status,
                            "failed": failed,
                        },
                    )
                )
            except Exception:
                logger.exception("linked-activity: failed to build check event for %s", check.id)

        reviews = (
            GitHubPullRequestReview.objects.filter(pull_request_id__in=pr_ids)
            .select_related("pull_request", "pull_request__mapping", "pull_request__mapping__connection")
            .order_by("-submitted_at", "-updated_at")[:PER_SOURCE_CAP]
        )
        for review in reviews:
            try:
                pull_request = review.pull_request
                state = (review.state or "").lower() or "submitted"
                title = f"Review {state} on PR #{pull_request.number}"
                if review.user_login:
                    title += f" by {review.user_login}"
                events.append(
                    make_event(
                        f"github-review-{review.id}",
                        "github",
                        "review",
                        title,
                        pull_request_url(pull_request),
                        review.user_login or None,
                        review.submitted_at or review.updated_at,
                        {"number": pull_request.number, "review_state": review.state},
                    )
                )
            except Exception:
                logger.exception("linked-activity: failed to build review event for %s", review.id)

    commit_links = GitHubCommitIssueLink.objects.filter(issue=issue).select_related(
        "commit", "commit__mapping", "commit__mapping__connection"
    )[:PER_SOURCE_CAP]
    for link in commit_links:
        try:
            commit = link.commit
            events.append(
                make_event(
                    f"github-commit-{commit.id}",
                    "github",
                    "commit",
                    f"Commit {commit.sha[:7]}: {first_line(commit.message) or 'commit'}",
                    commit_url(commit),
                    commit.author_name or commit.author_login or None,
                    commit.committed_at or commit.updated_at,
                    {"sha": commit.sha[:7], "repository": commit.mapping.full_name},
                )
            )
        except Exception:
            logger.exception("linked-activity: failed to build commit event for %s", link.id)

    return events


def asana_events(issue, project):
    """Events for Asana task links, comment links and sync logs of the project."""
    events = []

    task_links = (
        AsanaTaskLink.objects.filter(issue=issue)
        .select_related("sync", "sync__connection")
        .order_by("-created_at")[:PER_SOURCE_CAP]
    )
    for link in task_links:
        try:
            project_name = link.sync.asana_project_name or link.sync.asana_project_gid
            events.append(
                make_event(
                    f"asana-task_linked-{link.id}",
                    "asana",
                    "task_linked",
                    f"Task linked: {project_name}",
                    asana_task_url(link),
                    None,
                    link.created_at,
                    {"task_gid": link.asana_task_gid, "project_name": project_name},
                )
            )
        except Exception:
            logger.exception("linked-activity: failed to build task link event for %s", link.id)

    comment_links = (
        AsanaCommentLink.objects.filter(task_link__issue=issue)
        .select_related("task_link", "task_link__sync", "task_link__sync__connection")
        .order_by("-created_at")[:PER_SOURCE_CAP]
    )
    for link in comment_links:
        try:
            events.append(
                make_event(
                    f"asana-comment_synced-{link.id}",
                    "asana",
                    "comment_synced",
                    f"Comment synced ({link.direction}) with Asana",
                    asana_task_url(link.task_link),
                    None,
                    link.created_at,
                    {
                        "task_gid": link.task_link.asana_task_gid,
                        "story_gid": link.asana_story_gid,
                        "direction": link.direction,
                    },
                )
            )
        except Exception:
            logger.exception("linked-activity: failed to build comment link event for %s", link.id)

    # The sync log is project-scoped; issue rows are the story of this work
    # item, rows without an issue are the project-level sync heartbeat.
    logs = (
        AsanaSyncLog.objects.filter(project=project)
        .filter(Q(issue=issue) | Q(issue__isnull=True))
        .order_by("-created_at")[:PER_SOURCE_CAP]
    )
    for log in logs:
        try:
            if log.status == "error":
                event_type = "sync_error"
            elif log.status in ("skipped", "conflict"):
                event_type = "sync_warning"
            else:
                event_type = "sync_ok"
            title = f"Asana sync {log.status}: {log.entity_type}"
            if log.message:
                title += f" — {log.message}"
            events.append(
                make_event(
                    f"asana-{event_type}-{log.id}",
                    "asana",
                    event_type,
                    title,
                    None,
                    None,
                    log.created_at,
                    {"entity_type": log.entity_type, "direction": log.direction, "entity_gid": log.entity_gid},
                )
            )
        except Exception:
            logger.exception("linked-activity: failed to build sync log event for %s", log.id)

    return events


def slack_events(issue, project):
    """Events for linked Slack threads and the messages inside those threads."""
    events = []

    links = (
        SlackIssueLink.objects.filter(issue=issue, is_suppressed=False)
        .select_related("message", "message__mapping", "message__mapping__connection")
        .order_by("id")[:PER_SOURCE_CAP]
    )
    linked_ids = []
    seen_messages = set()
    for link in links:
        try:
            message = link.message
            linked_ids.append(message.id)
            seen_messages.add(message.id)
            mapping = message.mapping
            events.append(
                make_event(
                    f"slack-thread_linked-{link.id}",
                    "slack",
                    "thread_linked",
                    f"Slack thread linked: #{mapping.channel_name}",
                    slack_message_url(message),
                    None,
                    message.posted_at or message.remote_updated_at or message.updated_at,
                    {"channel": mapping.channel_name, "channel_id": mapping.channel_id, "ts": message.ts},
                )
            )
        except Exception:
            logger.exception("linked-activity: failed to build thread link event for %s", link.id)

    if linked_ids:
        thread_roots = {}
        for message in SlackMessage.objects.filter(id__in=linked_ids).only("id", "mapping_id", "ts"):
            thread_roots.setdefault(message.mapping_id, set()).add(message.ts)
        thread_filter = Q()
        for mapping_id, roots in thread_roots.items():
            thread_filter |= Q(mapping_id=mapping_id, thread_ts__in=roots)
        replies = (
            SlackMessage.objects.filter(thread_filter, is_deleted=False)
            .exclude(id__in=linked_ids)
            .select_related("mapping", "mapping__connection")
            .order_by("-posted_at", "-updated_at")[:PER_SOURCE_CAP]
        )
        for message in replies:
            try:
                if message.id in seen_messages:
                    continue
                seen_messages.add(message.id)
                mapping = message.mapping
                text = first_line(message.text) or f"Message in #{mapping.channel_name}"
                events.append(
                    make_event(
                        f"slack-slack_message-{message.id}",
                        "slack",
                        "slack_message",
                        text,
                        slack_message_url(message),
                        message.user_id or None,
                        message.posted_at or message.remote_updated_at or message.updated_at,
                        {
                            "channel": mapping.channel_name,
                            "channel_id": mapping.channel_id,
                            "ts": message.ts,
                            "user_id": message.user_id,
                        },
                    )
                )
            except Exception:
                logger.exception("linked-activity: failed to build message event for %s", message.id)

    return events


BUILDERS = {"github": github_events, "asana": asana_events, "slack": slack_events}


class LinkedActivityEndpoint(BaseAPIView):
    """Read-only cross-platform linked-activity feed for one work item."""

    def get(self, request, slug, project_id, issue_id):
        project = project_member(request.user, slug, project_id)
        issue = get_object_or_404(Issue, id=issue_id, project=project)

        source = request.query_params.get("source")
        sources = [item for item in KNOWN_SOURCES if item == source] or list(KNOWN_SOURCES)

        events = []
        for item in sources:
            try:
                events.extend(BUILDERS[item](issue, project))
            except Exception:
                logger.exception("linked-activity: source %s failed for issue %s", item, issue.id)

        # Rows without any timestamp cannot be placed on a timeline — drop them.
        events = [event for event in events if event.get("timestamp")]
        events.sort(key=lambda event: event["timestamp"], reverse=True)
        merged_total = len(events)

        params = request.query_params
        try:
            offset = max(int(params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = min(max(int(params.get("limit", 50)), 1), 200)
        except (TypeError, ValueError):
            limit = 50

        results = []
        for event in events[offset : offset + limit]:
            try:
                row = dict(event)
                row["timestamp"] = iso(row["timestamp"])
                results.append(row)
            except Exception:
                logger.exception("linked-activity: failed to serialize event %s", event.get("id"))

        return Response(
            {"count": merged_total, "offset": offset, "limit": limit, "results": results},
            status=status.HTTP_200_OK,
        )
