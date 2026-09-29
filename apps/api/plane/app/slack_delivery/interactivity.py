# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

"""Handlers for block_actions arriving from Work Object card buttons.

The interactivity endpoint verifies Slack's signature and enqueues; all
Slack/Django work happens here in the celery task. Actions carried on an
unfurl answer through their response_url, which Slack restricts to ephemeral
messages — every reply below is ephemeral by construction.
"""

import logging
import uuid

from crum import impersonate
from rest_framework.exceptions import ValidationError

from plane.db.models import Issue, IssueAssignee, IssueComment
from plane.db.models.slack_delivery import SlackConnection
from . import commands, services
from .client import SlackUnavailable, bot_token, post_response_url

logger = logging.getLogger(__name__)

MAX_ACTIONS = 5
SUMMARY_COMMENTS = 10
COMMENT_PREVIEW_CHARS = 4000
ACTION_IDS = ("plane:summarize", "plane:assign-me")


def parse(payload):
    """Extract (team_id, user_id, actions, response_url) from a block_actions
    payload, or None when the payload is not one we can act on."""
    if not isinstance(payload, dict) or payload.get("type") != "block_actions":
        return None
    team = payload.get("team") if isinstance(payload.get("team"), dict) else {}
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        return None
    response_url = payload.get("response_url")
    if not (isinstance(response_url, str) and response_url.startswith(commands.RESPONSE_URL_PREFIX)):
        response_url = ""
    try:
        team_id = services.slack_id(team.get("id"))
        user_id = services.slack_id(user.get("id"))
    except ValidationError:
        return None
    return {
        "team_id": team_id,
        "user_id": user_id,
        "actions": actions[:MAX_ACTIONS],
        "response_url": response_url,
    }


def _issue_for_value(connection, value):
    try:
        issue_id = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
    return (
        Issue.objects.select_related("project", "project__workspace", "state")
        .prefetch_related("labels")
        .filter(id=issue_id, project__workspace_id=connection.workspace_id)
        .first()
    )


def _summary_sources(issue):
    sources = []
    description = (issue.description_stripped or "").strip()
    if description:
        sources.append({"title": "Description", "content": description[:10000]})
    comments = (
        IssueComment.objects.filter(issue=issue, workspace_id=issue.workspace_id)
        .order_by("-created_at")
        .values_list("comment_stripped", flat=True)[:SUMMARY_COMMENTS]
    )
    for index, comment in enumerate(comments, start=1):
        text = (comment or "").strip()
        if text:
            sources.append({"title": f"Comment {index}", "content": text[:COMMENT_PREVIEW_CHARS]})
    return sources


def _summarize(connection, actor, issue):
    from plane.app.release_intelligence.provider import IntelligenceError, summarize_issue

    sources = _summary_sources(issue)
    try:
        result = summarize_issue(issue.project, f"{commands.issue_key(issue)} {issue.name}", sources)
    except IntelligenceError as error:
        # Configuration gaps and provider outages are user-visible, not retries.
        return {"response_type": "ephemeral", "text": str(error)}
    key = commands.issue_key(issue)
    url = commands.board_link(issue)
    head = f"*{key} · {commands.slack_escape(issue.name)}*"
    body = result["text"].strip()
    return {"response_type": "ephemeral", "text": f"{head}\n{body}\n{url}"}


def _assign_to_me(connection, actor, issue):
    from django.db import IntegrityError

    try:
        _, created = IssueAssignee.objects.get_or_create(
            issue=issue,
            assignee_id=actor.id,
            defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id},
        )
    except IntegrityError:
        return {"response_type": "ephemeral", "text": "Plane could not save that assignment. Try again."}
    verb = "You are now assigned to" if created else "You were already assigned to"
    return {
        "response_type": "ephemeral",
        "text": f"{verb} {commands.issue_key(issue)} · {commands.slack_escape(issue.name)}",
    }


def run(payload):
    """Execute one block_actions payload and post the answer to its response_url."""
    parsed = parse(payload)
    if parsed is None:
        return
    response_url = parsed["response_url"]
    try:
        connection = SlackConnection.objects.select_related("workspace").filter(
            team_id=parsed["team_id"], is_active=True
        ).first()
        if connection is None:
            raise commands.CommandError("This Slack workspace is not connected to a Plane workspace yet.")
        token = bot_token(connection)
        actor = commands.actor_user(connection, token, parsed["user_id"])
        response = None
        for action in parsed["actions"]:
            if not isinstance(action, dict) or action.get("action_id") not in ACTION_IDS:
                response = {"response_type": "ephemeral", "text": "That button is no longer available."}
                continue
            issue = _issue_for_value(connection, action.get("value"))
            if issue is None:
                response = {"response_type": "ephemeral", "text": "That Plane work item does not exist in this workspace."}
                continue
            # Attribute the write to the Slack actor like slash commands do.
            with impersonate(actor):
                if action["action_id"] == "plane:summarize":
                    response = _summarize(connection, actor, issue)
                else:
                    response = _assign_to_me(connection, actor, issue)
    except (commands.CommandError, SlackUnavailable) as error:
        response = {"response_type": "ephemeral", "text": str(error)}
    except Exception:
        logger.exception("slack interactivity failed")
        response = {"response_type": "ephemeral", "text": "Plane could not complete that action. Try again."}
    if response_url and response:
        post_response_url(response_url, response)
