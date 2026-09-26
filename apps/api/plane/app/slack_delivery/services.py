# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import re
from datetime import datetime, timedelta, timezone as datetime_timezone
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from plane.db.models import Issue
from plane.db.models.slack_delivery import (
    SlackConnection,
    SlackChannelMapping,
    SlackMessage,
    SlackIssueLink,
    SlackEventDelivery,
)

# Slack message subtypes that carry no conversational evidence.
SKIP_SUBTYPES = {
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "channel_archive",
    "channel_unarchive",
    "group_join",
    "group_leave",
    "group_topic",
    "group_purpose",
    "group_name",
    "group_archive",
    "group_unarchive",
    "bot_add",
    "bot_remove",
    "pin_added",
    "pin_removed",
    "reminder_add",
    "sh_room_created",
    "snippet_reply",  # snippet content is delivered as a file, not text evidence
}

DELIVERY_RETENTION = timedelta(hours=24)
MAX_PROCESSING_ATTEMPTS = 5
CONTENT_EVENTS = {"message"}


def slack_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{5,30}", value):
        raise ValidationError("Slack returned an invalid identifier.")
    return value


def message_ts(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6,20}(\.[0-9]{1,10})?", value):
        raise ValidationError("Slack returned an invalid message timestamp.")
    return value


def channel_name(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 80 or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValidationError("Slack returned an invalid channel name.")
    return value


def timestamp(value):
    if not isinstance(value, str):
        return None
    seconds = value.split(".", 1)[0]
    if not re.fullmatch(r"[0-9]{6,20}", seconds):
        return None
    try:
        result = datetime.fromtimestamp(int(seconds), tz=datetime_timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return result


def text(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def iso(value):
    return value.isoformat() if value else None


def message_url(message):
    domain = message.mapping.connection.team_domain
    if not domain:
        return None
    return f"https://{domain}.slack.com/archives/{message.mapping.channel_id}/p{message.ts.replace('.', '')}"


def message_data(message, *, manual=False):
    return {
        "id": str(message.id),
        "channel": message.mapping.channel_name,
        "channel_id": message.mapping.channel_id,
        "user": message.user_id,
        "text": message.text,
        "thread_ts": message.thread_ts or None,
        "url": message_url(message),
        "posted_at": iso(message.posted_at),
        "deleted": message.is_deleted,
        "linked_manually": manual,
        "connected": message.mapping.is_active and message.mapping.connection.is_active,
    }


def project_messages(project):
    return SlackMessage.objects.filter(
        mapping__project=project, mapping__connection__workspace_id=project.workspace_id, is_deleted=False
    )


def list_issue_messages(issue):
    links = (
        SlackIssueLink.objects.filter(
            issue=issue,
            is_suppressed=False,
            message__is_deleted=False,
            message__mapping__project_id=issue.project_id,
            message__mapping__connection__workspace_id=issue.workspace_id,
        )
        .select_related("message__mapping__connection")
        .order_by("-message__posted_at")
    )
    return [message_data(link.message, manual=link.is_manual) for link in links]


def list_project_messages(project):
    messages = (
        project_messages(project).select_related("mapping__connection").order_by("-posted_at", "-updated_at")[:200]
    )
    return [message_data(message) for message in messages]


def validate_project_messages(project, ids):
    try:
        wanted = {UUID(str(value)) for value in ids}
    except (ValueError, TypeError, AttributeError):
        return False
    return len(wanted) <= 100 and project_messages(project).filter(pk__in=wanted).count() == len(wanted)


def get_conversation_sources(project, message_ids):
    if len(message_ids) > 100 or not validate_project_messages(project, set(message_ids)):
        raise ValidationError("Select Slack messages from this project only (up to 100).")
    sources = []
    for message in (
        project_messages(project)
        .filter(pk__in=message_ids, is_deleted=False)
        .select_related("mapping__connection")
        .order_by("posted_at")
    ):
        sources.append(
            {
                "type": "slack_message",
                "id": str(message.id),
                "title": f"#{message.mapping.channel_name} — {message.user_id or 'unknown member'}",
                "text": message.text[:20000],
                "url": message_url(message),
            }
        )
    return sources


def reconcile_links(message):
    project = message.mapping.project
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(project.identifier)}-(\d+)(?![A-Za-z0-9_])"
    values = {
        int(match)
        for match in re.findall(pattern, message.text, re.IGNORECASE)
        if len(match) <= 10
    }
    issues = Issue.objects.filter(
        project=project, workspace_id=message.mapping.connection.workspace_id, sequence_id__in=sorted(values)[:100]
    )
    issue_ids = set(issues.values_list("id", flat=True))
    SlackIssueLink.objects.filter(message=message, is_manual=False, is_suppressed=False).exclude(
        issue_id__in=issue_ids
    ).delete()
    for issue_id in issue_ids:
        SlackIssueLink.objects.get_or_create(message=message, issue_id=issue_id)


def upsert_message(mapping, data, *, deleted=False, received_at=None):
    if not isinstance(data, dict):
        raise ValidationError("Invalid Slack message payload.")
    if slack_id(data.get("channel")) != mapping.channel_id:
        raise ValidationError("Message channel does not match its mapping.")
    ts = message_ts(data.get("ts"))
    remote = timestamp(data.get("ts")) or received_at or timezone.now()
    message, created = SlackMessage.objects.get_or_create(mapping=mapping, ts=ts)
    if not created and message.remote_updated_at and remote < message.remote_updated_at:
        return message
    message.user_id = text(data.get("user"), 32)
    message.thread_ts = text(data.get("thread_ts"), 32)
    message.text = text(data.get("text"), 20000)
    message.is_deleted = deleted
    message.posted_at = timestamp(data.get("ts")) or message.posted_at
    message.remote_updated_at = remote
    message.save()
    if not deleted:
        reconcile_links(message)
    return message


def upsert_channel_rename(connection, channel_id, name):
    if slack_id(channel_id) and channel_name(name):
        SlackChannelMapping.objects.filter(connection=connection, channel_id=channel_id).update(channel_name=name)


def process_delivery(delivery_id):
    with transaction.atomic():
        delivery = SlackEventDelivery.objects.select_for_update().get(id=delivery_id)
        if delivery.status in ("processed", "ignored", "failed"):
            return
        now = timezone.now()
        # Deliveries accepted before the lookup fields existed keep their
        # original binding; populate only from the authenticated stored body.
        changed = []
        try:
            if delivery.team_id is None:
                delivery.team_id = slack_id(delivery.payload.get("team_id"))
                changed.append("team_id")
            if delivery.event in CONTENT_EVENTS and delivery.channel_id is None:
                delivery.channel_id = slack_id(delivery.payload.get("event", {}).get("channel"))
                changed.append("channel_id")
        except (ValidationError, TypeError, ValueError, AttributeError):
            delivery.status = "failed"
            delivery.error = "InvalidStoredPayload"
            delivery.payload = {}
            delivery.processed_at = now
            delivery.save(update_fields=["status", "error", "payload", "processed_at"])
            return
        if changed:
            delivery.save(update_fields=changed)
        if delivery.received_at < now - DELIVERY_RETENTION:
            delivery.status = "ignored"
            delivery.error = "Expired"
        elif delivery.next_retry_at and delivery.next_retry_at > now:
            return
        else:
            # Only never-bound waiting events may acquire a connection. A
            # deleted or disconnected connection can never rebind an event.
            if delivery.connection_id:
                connection = SlackConnection.objects.select_for_update().filter(id=delivery.connection_id).first()
            elif delivery.status == "waiting":
                connection = (
                    SlackConnection.objects.select_for_update()
                    .filter(
                        team_id=delivery.team_id,
                    )
                    .first()
                )
                if not connection:
                    return
                delivery.connection = connection
                delivery.status = "awaiting_mapping"
            else:
                connection = None
            if not connection or not connection.is_active:
                delivery.status = "ignored"
            elif (
                delivery.event in CONTENT_EVENTS
                and not SlackChannelMapping.objects.filter(
                    connection=connection,
                    channel_id=delivery.channel_id,
                    is_active=True,
                    project__workspace_id=connection.workspace_id,
                    project__deleted_at__isnull=True,
                ).exists()
            ):
                # Explicitly disconnected channels stay disconnected. Only a
                # channel never mapped in this workspace can wait for setup.
                if SlackChannelMapping.objects.filter(
                    connection=connection,
                    channel_id=delivery.channel_id,
                ).exists():
                    delivery.status = "ignored"
                else:
                    delivery.status = "awaiting_mapping"
                    delivery.save(update_fields=["connection", "status"])
                    return
            else:
                delivery.processing_attempts += 1
                try:
                    with transaction.atomic():
                        apply_delivery(connection, delivery)
                    delivery.status = "processed"
                    delivery.error = ""
                    delivery.next_retry_at = None
                except (ValidationError, TypeError, ValueError, AttributeError, KeyError) as error:
                    # Retrying identical invalid input cannot repair it.
                    delivery.status = "failed"
                    delivery.error = type(error).__name__
                except Exception as error:
                    # Savepoint rollback removes partial projections before the
                    # durable retry is recorded. Beat also survives worker loss.
                    delivery.status = "retry" if delivery.processing_attempts < MAX_PROCESSING_ATTEMPTS else "failed"
                    delivery.error = type(error).__name__[:100]
                    delivery.next_retry_at = now + timedelta(
                        seconds=min(60 * 2 ** (delivery.processing_attempts - 1), 3600)
                    )
        delivery.processed_at = now
        if delivery.status in ("processed", "ignored", "failed"):
            delivery.payload = {}
            delivery.next_retry_at = None
        delivery.save(
            update_fields=[
                "connection",
                "status",
                "error",
                "processed_at",
                "payload",
                "processing_attempts",
                "next_retry_at",
            ]
        )


def apply_delivery(connection, delivery):
    payload = delivery.payload
    if slack_id(payload.get("team_id")) != connection.team_id:
        raise ValidationError("Workspace mismatch.")
    event = payload.get("event")
    if not isinstance(event, dict):
        raise ValidationError("Invalid Slack event.")
    if delivery.event in ("app_uninstalled", "tokens_revoked"):
        connection.is_active = False
        connection.save(update_fields=["is_active", "updated_at"])
        SlackChannelMapping.objects.filter(connection=connection).update(is_active=False)
        return
    if delivery.event == "channel_rename":
        upsert_channel_rename(connection, event.get("channel"), event.get("name"))
        return
    if delivery.event == "link_shared":
        from .unfurl import process_unfurl

        process_unfurl(connection, event)
        return
    channel_id = slack_id(event.get("channel"))
    mapping = (
        SlackChannelMapping.objects.select_for_update()
        .filter(
            connection=connection,
            channel_id=channel_id,
            is_active=True,
            project__workspace_id=connection.workspace_id,
            project__deleted_at__isnull=True,
        )
        .select_related("project", "connection")
        .first()
    )
    if not mapping:
        return
    subtype = event.get("subtype")
    if subtype == "message_deleted":
        deleted_ts = message_ts(event.get("deleted_ts"))
        SlackMessage.objects.filter(mapping=mapping, ts=deleted_ts).update(is_deleted=True, remote_updated_at=timezone.now())
        return
    if subtype in SKIP_SUBTYPES:
        return
    data = event.get("message") if subtype == "message_changed" else event
    upsert_message(mapping, data, received_at=delivery.received_at)


def sync_mapping(mapping_id):
    from .client import SlackClient, bot_token

    mapping = (
        SlackChannelMapping.objects.select_related("connection", "project")
        .filter(
            id=mapping_id,
            is_active=True,
            connection__is_active=True,
        )
        .first()
    )
    if not mapping:
        return
    fetched_at = timezone.now()
    messages = SlackClient().channel_history(bot_token(mapping.connection), mapping.channel_id)
    if not isinstance(messages, list):
        raise ValidationError("Slack returned invalid channel data.")
    with transaction.atomic():
        connection = SlackConnection.objects.select_for_update().get(id=mapping.connection_id)
        mapping = (
            SlackChannelMapping.objects.select_for_update()
            .select_related("connection", "project")
            .get(id=mapping_id)
        )
        if not connection.is_active or not mapping.is_active or mapping.project.workspace_id != connection.workspace_id:
            return
        for data in messages[:100]:
            if not isinstance(data, dict) or data.get("subtype") in SKIP_SUBTYPES:
                continue
            # conversations.history responses omit the channel; scope the
            # upsert to this mapping explicitly.
            upsert_message(mapping, {**data, "channel": mapping.channel_id}, received_at=fetched_at)
        mapping.sync_status = "synced"
        mapping.sync_error = ""
        mapping.last_synced_at = timezone.now()
        mapping.save(update_fields=["sync_status", "sync_error", "last_synced_at"])
