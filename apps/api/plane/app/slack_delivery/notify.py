# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Post work item changes to mapped Slack channels.

Receivers capture pre-change values, classify the committed change, and enqueue
one celery task per (mapping, event); all Slack network I/O stays inside the
task. Write paths that touch issues directly (commands, automations) notify like
any other writer; ``suppress_notifications`` exists for writers that must not
echo. Bulk assignee replacement (serializer bulk_create) bypasses model signals,
so only row-level and M2M assignee writes are classified as "assigned".
"""

import re
from contextvars import ContextVar
from contextlib import contextmanager

from django.conf import settings
from django.db import transaction
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from plane.utils.exception_logger import log_exception

# Event types map 1:1 onto SlackChannelMapping.notify_<type> toggles.
NOTIFY_EVENTS = ("created", "state_changed", "assigned", "commented")

COMMENT_PREVIEW_CHARS = 120
LABELS_PER_MESSAGE = 5
_suppressed = ContextVar("slack_notify_suppressed", default=False)


@contextmanager
def suppress_notifications():
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)


def escape_mrkdwn(value):
    """Make arbitrary user text inert inside mrkdwn: entity-escape link/HTML
    syntax, backslash-escape formatting characters."""
    text = str(value or "")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([*_`~])", r"\\\1", text)


def single_line(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def preview(value, limit=COMMENT_PREVIEW_CHARS):
    return single_line(value)[:limit]


def _board_url(workspace):
    from .client import configuration

    base = configuration()["BASE_URL"] or getattr(settings, "WEB_URL", "")
    return f"{base.rstrip('/')}/{workspace.slug}"


def _event(issue, event_type, **extra):
    from crum import get_current_user

    user = get_current_user()
    actor = user if getattr(user, "is_authenticated", False) else None
    return {
        "type": event_type,
        "issue_id": str(issue.id),
        "workspace_id": str(issue.workspace_id),
        "project_id": str(issue.project_id),
        "actor_id": str(actor.id) if actor else None,
        **extra,
    }


def _comment_event(comment):
    return {
        "type": "commented",
        "issue_id": str(comment.issue_id),
        "workspace_id": str(comment.workspace_id),
        "project_id": str(comment.project_id),
        "actor_id": str(comment.actor_id) if comment.actor_id else None,
        "comment_id": str(comment.id),
    }


def _schedule(issue, event):
    """Enqueue delivery per listening mapping; resolved again inside the task
    so disconnects and toggle changes between commit and run stay honored."""
    from plane.db.models.slack_delivery import SlackChannelMapping

    mappings = SlackChannelMapping.objects.filter(
        project_id=issue.project_id,
        is_active=True,
        connection__is_active=True,
        connection__workspace_id=issue.workspace_id,
        **{f"notify_{event['type']}": True},
    ).values_list("id", flat=True)
    mapping_ids = [str(value) for value in mappings]
    if not mapping_ids:
        return
    from .tasks import QUEUE, deliver_slack_notification

    for mapping_id in mapping_ids:
        transaction.on_commit(
            lambda mid=mapping_id: deliver_slack_notification.apply_async(args=[mid, event], queue=QUEUE)
        )


@receiver(pre_save, dispatch_uid="slack_notify_issue_pre_save")
def issue_pre_save(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw or _suppressed.get() or sender._meta.label_lower != "db.issue":
        return
    instance._slack_notify_old_state = None
    if instance._state.adding or (
        update_fields is not None and not {"state", "state_id"}.intersection(update_fields)
    ):
        return
    instance._slack_notify_old_state = (
        sender.objects.filter(pk=instance.pk).values_list("state_id", flat=True).first()
    )


@receiver(post_save, dispatch_uid="slack_notify_issue_post_save")
def issue_post_save(sender, instance, created, raw=False, **kwargs):
    if raw or _suppressed.get() or sender._meta.label_lower != "db.issue":
        return
    if created:
        _schedule(instance, _event(instance, "created"))
        return
    # Absent snapshot means the save could not have touched state.
    old = getattr(instance, "_slack_notify_old_state", None)
    if old is not None and old != instance.state_id:
        _schedule(
            instance,
            _event(
                instance,
                "state_changed",
                old_state_id=str(old),
                new_state_id=str(instance.state_id) if instance.state_id else None,
            ),
        )


@receiver(post_save, dispatch_uid="slack_notify_issue_assignee_post_save")
def issue_assignee_post_save(sender, instance, created, raw=False, **kwargs):
    if raw or not created or _suppressed.get() or sender._meta.label_lower != "db.issueassignee":
        return
    issue = instance.issue
    _schedule(issue, _event(issue, "assigned", assignee_ids=[str(instance.assignee_id)]))


@receiver(m2m_changed, dispatch_uid="slack_notify_issue_assignees_m2m")
def issue_assignees_m2m_changed(sender, instance, action, reverse=False, pk_set=None, **kwargs):
    if _suppressed.get() or action != "post_add" or sender._meta.label_lower != "db.issueassignee":
        return
    if reverse:
        from plane.db.models import Issue

        for issue in Issue.objects.filter(pk__in=pk_set or []):
            _schedule(issue, _event(issue, "assigned", assignee_ids=[str(instance.pk)]))
    else:
        for member in pk_set or []:
            _schedule(instance, _event(instance, "assigned", assignee_ids=[str(member)]))


@receiver(post_save, dispatch_uid="slack_notify_comment_post_save")
def comment_post_save(sender, instance, created, raw=False, **kwargs):
    if raw or not created or _suppressed.get() or sender._meta.label_lower != "db.issuecomment":
        return
    _schedule(instance, _comment_event(instance))


def _display_name(user):
    return (
        (user.display_name or "").strip()
        or (user.first_name or "").strip()
        or (user.username or "")
        or "someone"
    )


def _actor_name(actor_id, fallback):
    from plane.db.models import User

    if actor_id:
        user = User.objects.filter(id=actor_id).first()
        if user:
            return _display_name(user)
    return _display_name(fallback) if fallback else "someone"


def _assignee_names(assignee_ids):
    from plane.db.models import User

    wanted = [value for value in (assignee_ids or []) if value][:LABELS_PER_MESSAGE]
    names = {str(user.id): _display_name(user) for user in User.objects.filter(id__in=wanted)}
    return [names.get(value, "someone") for value in wanted]


def _state_names(event):
    from plane.db.models import State

    ids = [value for value in (event.get("old_state_id"), event.get("new_state_id")) if value]
    names = {str(key): value for key, value in State.objects.filter(id__in=ids).values_list("id", "name")}
    old = names.get(event.get("old_state_id")) or "unknown state"
    new = names.get(event.get("new_state_id")) or "unknown state"
    return single_line(old), single_line(new)


def _label_names(issue_id):
    from plane.db.models import IssueLabel

    return [
        single_line(name)
        for name in IssueLabel.objects.filter(issue_id=issue_id, deleted_at__isnull=True)
        .select_related("label")
        .values_list("label__name", flat=True)[:LABELS_PER_MESSAGE]
    ]


def build_blocks(mapping, issue, event):
    """Compose the Block Kit message (blocks + plain fallback) for one event."""
    project = mapping.project
    identifier = (project.identifier or "").strip()
    ident_seq = f"{identifier}-{issue.sequence_id}" if identifier else f"#{issue.sequence_id}"
    title = single_line(issue.name)
    event_type = event["type"]

    if event_type == "created":
        raw_line = f"Created by {_actor_name(event.get('actor_id'), issue.created_by)}"
    elif event_type == "state_changed":
        old_name, new_name = _state_names(event)
        raw_line = f"State: {old_name} → {new_name}"
    elif event_type == "assigned":
        raw_line = f"Assigned to {', '.join(_assignee_names(event.get('assignee_ids'))) or 'someone'}"
    elif event_type == "commented":
        from plane.db.models import IssueComment

        comment = IssueComment.objects.filter(id=event.get("comment_id")).first()
        body = preview(comment.comment_stripped) if comment else ""
        raw_line = f"Commented by {_actor_name(event.get('actor_id') or (comment.actor_id if comment else None), issue.created_by)}"
        if body:
            raw_line = f"{raw_line}: {body}"
    else:
        return None

    labels = _label_names(issue.id)
    url = f"{_board_url(project.workspace)}/projects/{project.id}/issues/{issue.id}"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{escape_mrkdwn(ident_seq)} · {escape_mrkdwn(title)}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": escape_mrkdwn(raw_line)}},
    ]
    context = [{"type": "mrkdwn", "text": f"<{url}|Open in Plane>"}]
    if labels:
        context.insert(0, {"type": "mrkdwn", "text": escape_mrkdwn(", ".join(labels))})
    blocks.append({"type": "context", "elements": context})
    return blocks, f"[{ident_seq}] {title} — {raw_line}", url


def deliver_notification(mapping_id, event):
    """Deliver one notification for one mapping; safe to retry, never posts for
    mappings that are inactive, cross-workspace, or toggled off."""
    from plane.db.models import Issue
    from plane.db.models.slack_delivery import SlackChannelMapping

    from .client import SlackClient, SlackUnavailable, bot_token

    if not isinstance(event, dict) or event.get("type") not in NOTIFY_EVENTS:
        log_exception(ValueError(f"slack notify: invalid event {event!r}"), warning=True)
        return
    mapping = (
        SlackChannelMapping.objects.select_related("connection", "project", "project__workspace")
        .filter(
            id=mapping_id,
            is_active=True,
            connection__is_active=True,
            project__deleted_at__isnull=True,
            **{f"notify_{event['type']}": True},
        )
        .first()
    )
    # A mapping owned by another workspace can never receive its neighbor's events.
    if not mapping or mapping.connection.workspace_id != mapping.project.workspace_id:
        return
    issue = Issue.objects.filter(
        id=event.get("issue_id"),
        project_id=mapping.project_id,
        workspace_id=mapping.project.workspace_id,
    ).first()
    if not issue:
        return
    built = build_blocks(mapping, issue, event)
    if not built:
        return
    blocks, fallback_text, _url = built
    SlackClient().post_message(bot_token(mapping.connection), mapping.channel_id, blocks, fallback_text)
