# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid
from django.db import models
from django.db.models import Q


class GitHubConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    installation_id = models.PositiveBigIntegerField(unique=True)
    account_login = models.CharField(max_length=255)
    github_user_id = models.PositiveBigIntegerField()
    authorized_repository_ids = models.JSONField(default=list)
    connected_by = models.ForeignKey("db.User", null=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class GitHubConnectNonce(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    user = models.ForeignKey("db.User", on_delete=models.CASCADE)
    stage = models.CharField(max_length=16, default="installation")
    installation_id = models.PositiveBigIntegerField(null=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)


class GitHubRepositoryMapping(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(GitHubConnection, on_delete=models.CASCADE)
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE)
    repository_id = models.PositiveBigIntegerField()
    full_name = models.CharField(max_length=255)
    is_private = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    sync_status = models.CharField(max_length=16, default="pending")
    sync_error = models.CharField(max_length=200, blank=True)
    last_synced_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "repository_id"],
                condition=Q(is_active=True),
                name="github_active_repository_mapping",
            )
        ]


class GitHubPullRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mapping = models.ForeignKey(GitHubRepositoryMapping, on_delete=models.CASCADE)
    github_id = models.PositiveBigIntegerField()
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=1000)
    body = models.TextField(blank=True)
    head_ref = models.CharField(max_length=500, blank=True)
    state = models.CharField(max_length=16, default="open")
    draft = models.BooleanField(default=False)
    merged_at = models.DateTimeField(null=True)
    review_state = models.CharField(max_length=32, default="pending")
    reviewed_at = models.DateTimeField(null=True)
    remote_updated_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mapping", "number"], name="github_mapping_pr_number")]


class GitHubIssueLink(models.Model):
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE)
    pull_request = models.ForeignKey(GitHubPullRequest, on_delete=models.CASCADE)
    is_manual = models.BooleanField(default=False)
    is_suppressed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["issue", "pull_request"], name="github_issue_pr_link")]


class GitHubRelease(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mapping = models.ForeignKey(GitHubRepositoryMapping, on_delete=models.CASCADE)
    github_id = models.PositiveBigIntegerField()
    tag_name = models.CharField(max_length=255)
    name = models.CharField(max_length=1000, blank=True)
    body = models.TextField(blank=True)
    draft = models.BooleanField(default=False)
    prerelease = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True)
    is_deleted = models.BooleanField(default=False)
    remote_updated_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mapping", "github_id"], name="github_mapping_release")]


class GitHubWebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    connection = models.ForeignKey(GitHubConnection, null=True, on_delete=models.SET_NULL)
    installation_id = models.PositiveBigIntegerField(null=True, db_index=True)
    repository_id = models.PositiveBigIntegerField(null=True)
    processing_attempts = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True)
    event = models.CharField(max_length=64)
    body_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default="queued")
    error = models.CharField(max_length=100, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True)
