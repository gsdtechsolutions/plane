# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid
from django.db import models
from django.db.models import Q


class SlackConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    team_id = models.CharField(max_length=32, unique=True)
    team_name = models.CharField(max_length=255)
    team_domain = models.CharField(max_length=255, blank=True)
    slack_user_id = models.CharField(max_length=32)
    bot_user_id = models.CharField(max_length=32)
    # Bot token stored encrypted at rest (see plane.app.slack_delivery.client);
    # never returned by the API.
    bot_token_encrypted = models.TextField(blank=True)
    connected_by = models.ForeignKey("db.User", null=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class SlackConnectNonce(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    user = models.ForeignKey("db.User", on_delete=models.CASCADE)
    stage = models.CharField(max_length=16, default="authorization")
    team_id = models.CharField(max_length=32, null=True, blank=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)


class SlackChannelMapping(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(SlackConnection, on_delete=models.CASCADE)
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE)
    channel_id = models.CharField(max_length=32)
    channel_name = models.CharField(max_length=255)
    is_private = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    sync_status = models.CharField(max_length=16, default="pending")
    sync_error = models.CharField(max_length=200, blank=True)
    last_synced_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "channel_id"],
                condition=Q(is_active=True),
                name="slack_active_channel_mapping",
            )
        ]


class SlackMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mapping = models.ForeignKey(SlackChannelMapping, on_delete=models.CASCADE)
    ts = models.CharField(max_length=32)
    user_id = models.CharField(max_length=32, blank=True)
    thread_ts = models.CharField(max_length=32, blank=True)
    text = models.TextField(blank=True)
    is_deleted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True)
    remote_updated_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mapping", "ts"], name="slack_mapping_message_ts")]


class SlackIssueLink(models.Model):
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE)
    message = models.ForeignKey(SlackMessage, on_delete=models.CASCADE)
    is_manual = models.BooleanField(default=False)
    is_suppressed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["issue", "message"], name="slack_issue_message_link")]


class SlackEventDelivery(models.Model):
    # Primary key is the Slack event_id: stable across retries, so repeated
    # Event API delivery attempts deduplicate on insert.
    id = models.CharField(max_length=64, primary_key=True)
    connection = models.ForeignKey(SlackConnection, null=True, on_delete=models.SET_NULL)
    team_id = models.CharField(max_length=32, null=True, db_index=True)
    channel_id = models.CharField(max_length=32, null=True)
    processing_attempts = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True)
    event = models.CharField(max_length=64)
    body_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default="queued")
    error = models.CharField(max_length=100, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True)
