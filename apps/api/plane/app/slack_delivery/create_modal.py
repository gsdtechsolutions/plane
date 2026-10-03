# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Asana-style creation dialog for `/plane create`.

The slash command opens a modal instead of creating the issue outright; the
command endpoint must call views.open inline because a trigger_id expires long
before a celery hop could use it (the entity_details_requested constraint).
The view submission creates the issue, applies assignee / due date /
subscribers, then posts one card to the chosen channel. Creation notifications
are suppressed so the card is the single announcement, like the Asana app this
dialog clones.
"""

import hashlib
import html
import json
import logging
from datetime import date
from uuid import UUID

from crum import impersonate
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from plane.db.models import Issue, IssueAssignee, IssueSubscriber, Project, ProjectMember, State
from plane.db.models.slack_delivery import SlackEventDelivery
from . import commands, services
from .client import SlackClient, SlackUnavailable, bot_token, slack_blocks_escape
from .notify import suppress_notifications

logger = logging.getLogger(__name__)

CALLBACK_ID = "plane_create_issue"
DESCRIPTION_MAX = 3000
PROJECT_LIMIT = 100
COLLABORATOR_LIMIT = 50
SHARE_NOTE = "This will share the task title, assignee, due date, and description with a link to view it in Plane."
DESCRIPTION_PREVIEW_CHARS = 700


def actor_projects(connection, actor):
    """Projects the actor may create work items in, capped for the option list."""
    ids = ProjectMember.objects.filter(
        project__workspace_id=connection.workspace_id,
        member=actor,
        is_active=True,
        role__gte=15,
    ).values_list("project_id", flat=True)
    return list(Project.objects.filter(id__in=ids, deleted_at__isnull=True).order_by("name", "id")[:PROJECT_LIMIT])


def _field(block_id, label, element, *, optional=False):
    return {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": element,
        "optional": optional,
    }


def build_modal(connection, mapping, actor, title_text, channel_id, user_id):
    """Compose the New Plane task view; raises CommandError when the actor has
    no project to create in (the command endpoint renders that as an
    ephemeral message)."""
    projects = actor_projects(connection, actor)
    if not projects:
        raise commands.CommandError("You are not an active member of any project in this Plane workspace.")
    title_text = (title_text or "").strip()
    # `/plane create KEY <title>` preselects KEY's project instead of the
    # channel default, preserving the old syntax's targeting.
    parts = title_text.split(None, 1)
    initial_project = None
    if len(parts) == 2:
        candidate = commands.project_for_key(connection, parts[0])
        if candidate is not None and any(item.id == candidate.id for item in projects):
            initial_project = candidate
            title_text = parts[1]
    if initial_project is None and mapping is not None and any(item.id == mapping.project_id for item in projects):
        initial_project = mapping.project
    if initial_project is None:
        initial_project = projects[0]
    options = [
        {"text": {"type": "plain_text", "text": item.name[:75]}, "value": str(item.id)}
        for item in projects
    ]
    selected = next(item for item in options if item["value"] == str(initial_project.id))
    title_input = {"type": "plain_text_input", "action_id": "title", "max_length": commands.TITLE_MAX}
    if title_text:
        title_input["initial_value"] = title_text[: commands.TITLE_MAX]
    blocks = [
        _field("title", "Task name", title_input),
        _field(
            "assignee",
            "Assignee",
            {
                "type": "users_select",
                "action_id": "assignee",
                "placeholder": {"type": "plain_text", "text": "Unassigned"},
            },
            optional=True,
        ),
        _field(
            "project",
            "Project",
            {
                "type": "static_select",
                "action_id": "project",
                "options": options,
                "initial_option": selected,
            },
        ),
        _field(
            "due",
            "Due date",
            {
                "type": "datepicker",
                "action_id": "due",
                "placeholder": {"type": "plain_text", "text": "No date selected"},
            },
            optional=True,
        ),
        _field(
            "description",
            "Description",
            {
                "type": "plain_text_input",
                "action_id": "description",
                "multiline": True,
                "max_length": DESCRIPTION_MAX,
                "placeholder": {"type": "plain_text", "text": "Write a task description"},
            },
            optional=True,
        ),
        _field(
            "channel",
            "Slack channel",
            {
                "type": "conversations_select",
                "action_id": "channel",
                "initial_conversation": channel_id,
                "placeholder": {"type": "plain_text", "text": "Where should we share this?"},
            },
            optional=True,
        ),
        {"type": "section", "text": {"type": "mrkdwn", "text": SHARE_NOTE}},
        _field(
            "collaborators",
            "Collaborators",
            {
                "type": "multi_users_select",
                "action_id": "collaborators",
                "placeholder": {"type": "plain_text", "text": "Search for collaborators"},
            },
            optional=True,
        ),
    ]
    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "title": {"type": "plain_text", "text": "New Plane task"},
        "submit": {"type": "plain_text", "text": "Create"},
        "close": {"type": "plain_text", "text": "Close"},
        "notify_on_close": False,
        "private_metadata": json.dumps(
            {"team_id": connection.team_id, "channel_id": channel_id, "user_id": user_id}
        ),
        "blocks": blocks,
    }


def open_modal(connection, mapping, actor, token, trigger_id, title_text, channel_id, user_id):
    if not trigger_id:
        raise commands.CommandError("Slack did not provide a dialog token. Run the command again.")
    view = build_modal(connection, mapping, actor, title_text, channel_id, user_id)
    SlackClient().views_open(token, trigger_id, view)


def _member_for_profile(connection, profile):
    email = commands.profile_email(profile)
    return commands.workspace_member(connection, email) if email else None


def issue_card(issue, slack_user_id):
    """(blocks, fallback_text) for the created-work-item announcement."""
    url = commands.board_link(issue)
    key = commands.issue_key(issue)
    head = f"{key} · {slack_blocks_escape(issue.name)}"
    head_text = f"<{url}|{head}>" if url else head
    state = slack_blocks_escape(issue.state.name) if issue.state else "none"
    assignees = [
        commands.member_display(row.assignee)
        for row in IssueAssignee.objects.filter(issue=issue).select_related("assignee")
        if row.assignee
    ]
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"<@{slack_user_id}> created a new work item."}},
        {"type": "section", "text": {"type": "mrkdwn", "text": head_text}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Work item in Plane · {state}"}]},
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Assignee*\n{slack_blocks_escape(', '.join(assignees)) if assignees else 'Unassigned'}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Due date*\n{issue.target_date.isoformat() if issue.target_date else 'None'}",
                },
            ],
        },
    ]
    description = (issue.description_stripped or "").strip()
    if description:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "plain_text",
                    "text": slack_blocks_escape(description)[:DESCRIPTION_PREVIEW_CHARS],
                    "emoji": False,
                },
            }
        )
    actions = []
    if url:
        actions.append(
            {
                "type": "button",
                "action_id": "plane:open",
                "text": {"type": "plain_text", "text": "Open in Plane"},
                "url": url,
                "style": "primary",
            }
        )
    # Reuses the work-object card action: interactivity answers ephemerally.
    actions.append(
        {
            "type": "button",
            "action_id": "plane:assign-me",
            "text": {"type": "plain_text", "text": "Assign to me"},
            "value": str(issue.id),
        }
    )
    blocks.append({"type": "actions", "elements": actions})
    return blocks, f"{key} · {issue.name}\n{url}".rstrip()


def _link_card_message(connection, issue, channel, result, slack_user_id):
    """Record the card so it appears on the work item's Slack panel; only a
    channel mapped to the issue's project surfaces it (reconcile_links)."""
    mapping = commands.channel_mapping(connection, channel)
    if mapping is None or not isinstance(result, dict) or not result.get("ts"):
        return
    data = {
        "channel": channel,
        "ts": result.get("ts"),
        "user": slack_user_id,
        "text": ((result.get("message") or {}).get("text") or "")[:20000],
    }
    try:
        services.upsert_message(mapping, data)
    except ValidationError as error:
        logger.warning("slack create-modal message link failed: %s", error)


def submission(connection, payload):
    view = payload.get("view") or {}
    values = (view.get("state") or {}).get("values") or {}

    def value(name):
        return (values.get(name) or {}).get(name) or {}

    title = (value("title").get("value") or "").strip()
    description = value("description").get("value") or ""
    project_id = (value("project").get("selected_option") or {}).get("value")
    assignee_slack_id = value("assignee").get("selected_user")
    due_text = value("due").get("selected_date")
    channel = value("channel").get("selected_conversation")
    collaborator_slack_ids = value("collaborators").get("selected_users") or []

    errors = {}
    if not commands.TITLE_MIN <= len(title) <= commands.TITLE_MAX:
        errors["title"] = f"Give the task a title of {commands.TITLE_MIN} to {commands.TITLE_MAX} characters."
    if len(description) > DESCRIPTION_MAX:
        errors["description"] = f"Keep the description under {DESCRIPTION_MAX} characters."
    due = None
    if due_text:
        try:
            due = date.fromisoformat(due_text)
        except ValueError:
            errors["due"] = "Enter a valid date."
    if channel:
        try:
            services.slack_id(channel)
        except ValidationError:
            errors["channel"] = "Choose a channel this app can post to."
    try:
        metadata = json.loads(view.get("private_metadata") or "{}")
        if not isinstance(metadata, dict) or metadata.get("team_id") != connection.team_id:
            raise ValueError()
        origin_channel = services.slack_id(metadata.get("channel_id"))
        slack_user_id = services.slack_id(metadata.get("user_id"))
    except (ValueError, TypeError, KeyError):
        origin_channel = slack_user_id = ""
        errors["title"] = "The dialog context is invalid. Run /plane create again."

    actor = None
    token = ""
    if not errors:
        try:
            token = bot_token(connection)
            actor = commands.actor_user(connection, token, (payload.get("user") or {}).get("id"))
        except (commands.CommandError, SlackUnavailable) as error:
            errors["title"] = str(error)[:150]

    project = None
    if not errors:
        try:
            project = Project.objects.filter(
                id=UUID(str(project_id)), workspace_id=connection.workspace_id, deleted_at__isnull=True
            ).first()
        except (ValueError, TypeError):
            project = None
        if project is None:
            errors["project"] = "Choose a project in this workspace."
        elif not ProjectMember.objects.filter(
            project=project, member=actor, is_active=True, role__gte=15
        ).exists():
            errors["project"] = f"You are not an active member of project {project.identifier}."

    assignee = None
    if not errors and assignee_slack_id:
        try:
            profile = SlackClient().user_info(token, assignee_slack_id)
        except SlackUnavailable:
            errors["assignee"] = "Plane could not read that member's profile. Try again."
        else:
            assignee = _member_for_profile(connection, profile)
            if assignee is None:
                errors["assignee"] = "That Slack member is not a member of this Plane workspace."
    collaborators = []
    if not errors and collaborator_slack_ids:
        for slack_id in collaborator_slack_ids[:COLLABORATOR_LIMIT]:
            try:
                profile = SlackClient().user_info(token, slack_id)
            except SlackUnavailable:
                errors["collaborators"] = "Plane could not read those members' profiles. Try again."
                break
            member = _member_for_profile(connection, profile)
            if member is None:
                errors["collaborators"] = "One or more selected people are not members of this Plane workspace."
                break
            collaborators.append(member)
    if errors:
        return {"response_action": "errors", "errors": errors}

    # A view id stays stable across Slack's retries; invalid submissions never
    # claim it (mirrors the asks dialog). The prefix+digest must fit the 64-char id.
    identity = f"{connection.team_id}:{view.get('id', '')}"
    digest = hashlib.sha256(identity.encode()).hexdigest()
    with transaction.atomic():
        delivery, created = SlackEventDelivery.objects.get_or_create(
            id="create-modal-" + digest[:51],
            defaults={
                "connection": connection,
                "team_id": connection.team_id,
                "channel_id": origin_channel,
                "event": "view_submission",
                "body_hash": digest,
                "status": "queued",
            },
        )
        if not created:
            return {"response_action": "clear"}
        with suppress_notifications(), impersonate(actor):
            state = State.objects.filter(project=project).order_by("-default", "sequence").first()
            issue = Issue.objects.create(
                project=project,
                workspace=connection.workspace,
                name=title[: commands.TITLE_MAX],
                description_html="".join(
                    f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in description.split("\n\n")
                )
                if description.strip()
                else "",
                created_by=actor,
                state=state,
                target_date=due,
            )
            if assignee is not None:
                IssueAssignee.objects.get_or_create(
                    issue=issue,
                    assignee_id=assignee.id,
                    defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id},
                )
            for member in collaborators:
                # The through row carries project/workspace (ProjectBaseModel
                # derives workspace from project); plain get_or_create without
                # them fails the NOT NULL columns, like the assignee rows.
                IssueSubscriber.objects.get_or_create(
                    issue=issue,
                    subscriber=member,
                    defaults={"project_id": issue.project_id, "workspace_id": issue.workspace_id},
                )
        delivery.status = "processed"
        delivery.processed_at = timezone.now()
        delivery.save(update_fields=["status", "processed_at"])
    # The card is the single announcement: creation notifications were
    # suppressed above, and a failed card post falls back to an ephemeral
    # confirmation so the work item stays discoverable.
    client = SlackClient()
    blocks, fallback = issue_card(issue, slack_user_id)
    posted = False
    if channel:
        try:
            result = client.post_message(token, channel, blocks, fallback)
        except SlackUnavailable as error:
            logger.warning("slack create-modal card post failed: %s", error)
        else:
            posted = True
            _link_card_message(connection, issue, channel, result, slack_user_id)
    if not posted:
        try:
            client.post_ephemeral(
                token,
                origin_channel,
                slack_user_id,
                f"Created {commands.issue_key(issue)} · {commands.slack_escape(issue.name)}\n{commands.board_link(issue)}",
            )
        except SlackUnavailable as error:
            logger.warning("slack create-modal ephemeral confirmation failed: %s", error)
    return {"response_action": "clear"}


def handle(payload, raw):
    """Interactivity entry for view_submission with this dialog's callback."""
    from . import asks

    connection = asks.connection_for(payload)
    if connection is None:
        asks.ignored(payload, raw, error="No active connection")
        return {}
    return submission(connection, payload)
