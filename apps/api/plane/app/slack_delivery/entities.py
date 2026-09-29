# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

"""Slack Work Object builders for Plane work items.

A Work Object entity is what renders Jira-style cards: an app-branded header
(display_id + product icon), typed fields (status chip, priority, assignee,
description) and real action buttons, plus the click-through flexpane fed by
entity.presentDetails. Field names are Slack's canonical task-entity names —
custom spellings render but lose native styling. Limits follow the Work
Object docs: text values ≤3000 chars, ≤2 primary and ≤5 overflow actions.
"""

from plane.app.slack_delivery.client import slack_blocks_board_url, slack_blocks_base_url, slack_blocks_member_name

ENTITY_TYPE_TASK = "slack#/entities/task"
EXTERNAL_REF_TYPE = "plane_issue"

TITLE_MAX = 512
FIELD_TEXT_MAX = 3000
ACTION_VALUE_MAX = 2000
DESCRIPTION_MAX = FIELD_TEXT_MAX
MAX_ASSIGNEES_SHOWN = 3

ACTION_OPEN = "plane:open"
ACTION_SUMMARIZE = "plane:summarize"
ACTION_ASSIGN_ME = "plane:assign-me"
ACTION_MARK_DONE = "plane:mark-done"

# State groups are Plane's own taxonomy; the values are Slack tag colors.
STATE_GROUP_COLORS = {
    "backlog": "gray",
    "unstarted": "yellow",
    "started": "blue",
    "completed": "green",
    "cancelled": "red",
}


def _single_line(value):
    return " ".join(str(value or "").split())


def _clip(value, limit):
    value = str(value or "")
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def issue_key(issue):
    identifier = (issue.project.identifier or "").strip()
    return f"{identifier}-{issue.sequence_id}" if identifier else f"#{issue.sequence_id}"


def issue_assignees(issue):
    """IssueAssignee rows are what the Plane UI shows; the legacy assignees
    M2M can hold stale duplicates that never reach the UI."""
    from plane.db.models import IssueAssignee

    return [row.assignee for row in IssueAssignee.objects.filter(issue=issue).select_related("assignee") if row.assignee]


def issue_labels(issue):
    return [label.name for label in issue.labels.all()]


def product_icon_url(base_url):
    return f"{base_url.rstrip('/')}/plane-logos/gsd-logo.png" if base_url else None


def entity_url(issue, base_url=None):
    base = slack_blocks_base_url() if base_url is None else str(base_url or "").rstrip("/")
    return slack_blocks_board_url(issue, base)


def entity_attributes(issue, *, base_url=None):
    attributes = {
        "title": {"text": _clip(_single_line(issue.name), TITLE_MAX)},
        "display_id": issue_key(issue),
        "display_type": "Task",
        "product_name": "Plane",
    }
    icon = product_icon_url(base_url)
    if icon:
        attributes["product_icon"] = {"alt_text": "Plane", "url": icon}
    if issue.updated_at:
        # Slack refreshes the card only when this value grows, so block
        # actions can trigger a card refresh after a change.
        attributes["metadata_last_modified"] = int(issue.updated_at.timestamp())
    return attributes


def entity_fields(issue):
    fields = {}
    display_order = []
    if issue.state:
        status = {"value": _clip(issue.state.name, 100)}
        color = STATE_GROUP_COLORS.get(issue.state.group)
        if color:
            status["tag_color"] = color
        fields["status"] = status
        display_order.append("status")
    if issue.priority and issue.priority != "none":
        fields["priority"] = {"value": _clip(issue.priority.replace("_", " ").title(), 100)}
        display_order.append("priority")
    assignees = issue_assignees(issue)
    if len(assignees) == 1:
        user = {"text": slack_blocks_member_name(assignees[0])}
        if assignees[0].email:
            user["email"] = assignees[0].email
        # Slack's schema requires type:"user" on user-valued fields; entries
        # without it are rejected wholesale (error_processing_metadata).
        fields["assignee"] = {"type": "user", "user": user}
    elif assignees:
        names = ", ".join(slack_blocks_member_name(value) for value in assignees[:MAX_ASSIGNEES_SHOWN])
        if len(assignees) > MAX_ASSIGNEES_SHOWN:
            names += f" +{len(assignees) - MAX_ASSIGNEES_SHOWN}"
        fields["assignee"] = {"value": _clip(names, FIELD_TEXT_MAX)}
    if assignees:
        display_order.append("assignee")
    labels = issue_labels(issue)
    custom_fields = None
    if labels:
        custom_fields = [{"key": "labels", "label": "Labels", "value": _clip(", ".join(labels), FIELD_TEXT_MAX), "type": "string"}]
        display_order.append("labels")
    description = _clip((issue.description_stripped or "").strip(), DESCRIPTION_MAX)
    if description:
        fields["description"] = {"value": description}
        display_order.append("description")
    from plane.db.models import User

    creator = User.objects.filter(id=issue.created_by_id).first() if issue.created_by_id else None
    if creator is not None:
        fields["created_by"] = {"type": "user", "user": {"text": slack_blocks_member_name(creator)}}
        display_order.append("created_by")
    # Date fields accept unix timestamps (ISO strings fail the schema match).
    if issue.created_at:
        fields["date_created"] = {"value": int(issue.created_at.timestamp())}
        display_order.append("date_created")
    if issue.updated_at:
        fields["date_updated"] = {"value": int(issue.updated_at.timestamp())}
        display_order.append("date_updated")
    return fields, custom_fields, display_order


def entity_actions(issue):
    """Open is a plain link button; the others round-trip through the
    interactivity endpoint as block_actions carrying the issue id in value."""
    url = entity_url(issue)
    primary = []
    if url:
        primary.append(
            {
                "text": "Open in Plane",
                "action_id": ACTION_OPEN,
                "url": url[:3000],
                "style": "primary",
                "accessibility_label": f"Open {issue_key(issue)} in Plane",
            }
        )
    value = str(issue.id)[:ACTION_VALUE_MAX]
    return {
        "primary_actions": primary
        + [
            {
                "text": "✨ Summarize",
                "action_id": ACTION_SUMMARIZE,
                "value": value,
                "accessibility_label": f"Summarize {issue_key(issue)} with AI",
            }
        ],
        "overflow_actions": [
            {
                "text": "Assign to me",
                "action_id": ACTION_ASSIGN_ME,
                "value": value,
                "accessibility_label": f"Assign {issue_key(issue)} to yourself",
            },
            {
                "text": "Mark done",
                "action_id": ACTION_MARK_DONE,
                "value": value,
                "accessibility_label": f"Move {issue_key(issue)} to the completed state",
            },
        ],
    }


def entity_payload(issue, *, base_url=None):
    fields, custom_fields, display_order = entity_fields(issue)
    payload = {"attributes": entity_attributes(issue, base_url=base_url), "fields": fields, "display_order": display_order}
    if custom_fields:
        payload["custom_fields"] = custom_fields
    payload["actions"] = entity_actions(issue)
    return payload


def entity_for_issue(issue, *, base_url=None, app_unfurl_url=None):
    """One entity for chat.unfurl metadata / chat.postMessage / presentDetails."""
    entity = {
        "entity_type": ENTITY_TYPE_TASK,
        "external_ref": {"id": str(issue.id), "type": EXTERNAL_REF_TYPE},
        "url": entity_url(issue, base_url) or (app_unfurl_url or ""),
        "entity_payload": entity_payload(issue, base_url=base_url),
    }
    if app_unfurl_url:
        entity["app_unfurl_url"] = app_unfurl_url
    return entity


def unfurl_metadata(issues_and_urls):
    """chat.unfurl metadata: one entity per pasted link, keyed by nothing —
    Slack matches entities back to links via app_unfurl_url."""
    entities = [
        entity_for_issue(issue, app_unfurl_url=url) if url else entity_for_issue(issue)
        for issue, url in issues_and_urls
    ]
    return {"entities": entities}


def details_metadata(issue, *, base_url=None):
    """entity.presentDetails metadata: a single entity without the unfurl URL."""
    return entity_for_issue(issue, base_url=base_url)


def composer_preview(issue, *, base_url=None):
    """Small preview Slack shows while the link is still in the composer."""
    preview = {"title": {"type": "plain_text", "text": _clip(f"{issue_key(issue)} {_single_line(issue.name)}", TITLE_MAX)}}
    icon = product_icon_url(base_url)
    if icon:
        preview["icon_url"] = icon
    return preview
