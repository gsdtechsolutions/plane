# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
import logging
import os

from celery import shared_task

logger = logging.getLogger(__name__)

QUEUE = os.environ.get("SLACK_DELIVERY_QUEUE", "slack-delivery")


@shared_task(queue=QUEUE, name="slack_delivery.process", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_slack_event(delivery_id):
    from .services import process_delivery

    process_delivery(delivery_id)


@shared_task(queue=QUEUE, name="slack_delivery.sync_mapping")
def sync_slack_mapping(mapping_id):
    from plane.db.models.slack_delivery import SlackChannelMapping
    from .services import sync_mapping

    try:
        sync_mapping(mapping_id)
    except Exception:
        SlackChannelMapping.objects.filter(id=mapping_id, is_active=True).update(
            sync_status="failed", sync_error="Slack could not sync this channel. Check the connection and retry."
        )
        raise


@shared_task(queue=QUEUE, name="slack_delivery.recover_pending")
def recover_pending_slack_events():
    """Recover committed events whose initial broker publication failed."""
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import Q, Exists, OuterRef, F
    from plane.db.models.slack_delivery import SlackEventDelivery, SlackChannelMapping

    from .services import DELIVERY_RETENTION

    now = timezone.now()
    # Bound retention independently of whether a Slack workspace ever connects.
    SlackEventDelivery.objects.filter(
        received_at__lt=now - DELIVERY_RETENTION,
        status__in=["waiting", "awaiting_mapping", "queued", "retry"],
    ).update(status="ignored", error="Expired", payload={}, next_retry_at=None, processed_at=now)
    pending = (
        SlackEventDelivery.objects.filter(
            Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now),
            status__in=["queued", "retry"],
            received_at__lt=now - timedelta(minutes=1),
        )
        .order_by("received_at")
        .values_list("id", flat=True)[:100]
    )
    eligible_mapping = SlackChannelMapping.objects.filter(
        is_active=True,
        connection__is_active=True,
        connection__team_id=OuterRef("team_id"),
        channel_id=OuterRef("channel_id"),
        project__workspace_id=F("connection__workspace_id"),
        project__deleted_at__isnull=True,
    )
    waiting = (
        SlackEventDelivery.objects.filter(
            status__in=["waiting", "awaiting_mapping"],
        )
        .annotate(has_mapping=Exists(eligible_mapping))
        .filter(has_mapping=True)
        .order_by(
            "received_at",
        )
        .values_list("id", flat=True)[:100]
    )
    for delivery_id in list(pending) + list(waiting):
        process_slack_event.delay(str(delivery_id))

    mappings = (
        SlackChannelMapping.objects.filter(
            sync_status="pending",
            is_active=True,
            connection__is_active=True,
            created_at__lt=timezone.now() - timedelta(minutes=1),
        )
        .order_by("created_at")
        .values_list("id", flat=True)[:20]
    )
    for mapping_id in mappings:
        sync_slack_mapping.delay(str(mapping_id))


@shared_task(queue=QUEUE, name="slack_delivery.command", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def run_slack_command(payload):
    """Execute a validated slash command and post the answer to its response_url."""
    from rest_framework.exceptions import PermissionDenied, ValidationError

    from . import commands
    from .client import SlackUnavailable, post_response_url

    try:
        response = commands.execute(payload)
    except (commands.CommandError, ValidationError, PermissionDenied, SlackUnavailable) as error:
        # Expected failures answer the user instead of burning retries.
        response = {"response_type": "ephemeral", "text": str(error)}
    post_response_url(payload.get("response_url") or "", response)


@shared_task(queue=QUEUE, name="slack_delivery.notify", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def deliver_slack_notification(mapping_id, event):
    """Post one work item event to one mapped channel; all Slack I/O lives here."""
    from .notify import deliver_notification

    deliver_notification(mapping_id, event)


@shared_task(queue=QUEUE, name="slack_delivery.presence", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def sync_slack_channel_presence():
    """Join every public channel so link unfurls work workspace-wide.

    Requires the channels:join scope on each connection's bot token; private
    channels still need a manual invite by design.
    """
    from plane.db.models.slack_delivery import SlackConnection

    from .client import SlackClient, SlackUnavailable, bot_token

    for connection in SlackConnection.objects.filter(is_active=True):
        try:
            token = bot_token(connection)
            channels = SlackClient().channels(token)
        except SlackUnavailable as error:
            logger.warning("slack presence: skipping connection %s: %s", connection.id, error)
            continue
        joined = 0
        for channel in channels:
            if channel.get("is_private") or channel.get("is_member") or channel.get("is_archived"):
                continue
            channel_id = channel.get("id")
            if isinstance(channel_id, str) and channel_id:
                try:
                    SlackClient().join_channel(token, channel_id)
                    joined += 1
                except SlackUnavailable as error:
                    logger.warning("slack presence: join %s failed: %s", channel.get("name"), error)
        if joined:
            logger.info("slack presence: joined %s public channels for %s", joined, connection.team_name)
