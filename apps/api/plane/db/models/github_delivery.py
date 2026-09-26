# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid
from django.db import models
from django.db.models import Q


class GitHubApp(models.Model):
    """A GitHub App created through the click-to-connect manifest flow.

    One row per connected account. Secrets (client secret, private key,
    webhook secret) are Fernet-encrypted at rest; see app.github_delivery.crypto.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    host = models.CharField(max_length=255, default="github.com")
    app_id = models.PositiveBigIntegerField()
    slug = models.CharField(max_length=100)
    client_id = models.CharField(max_length=100)
    client_secret = models.TextField()
    private_key = models.TextField()
    webhook_secret = models.TextField()
    created_by = models.ForeignKey("db.User", null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["host", "app_id"], name="github_app_host_app_id"),
        ]


class GitHubConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    app = models.ForeignKey(GitHubApp, null=True, on_delete=models.SET_NULL)
    host = models.CharField(max_length=255, default="github.com")
    installation_id = models.PositiveBigIntegerField()
    account_login = models.CharField(max_length=255)
    github_user_id = models.PositiveBigIntegerField()
    authorized_repository_ids = models.JSONField(default=list)
    connected_by = models.ForeignKey("db.User", null=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["host", "installation_id"], name="github_connection_host_installation"),
        ]


class GitHubConnectNonce(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE)
    user = models.ForeignKey("db.User", on_delete=models.CASCADE)
    stage = models.CharField(max_length=16, default="manifest")
    host = models.CharField(max_length=255, default="github.com")
    organization = models.CharField(max_length=39, blank=True, default="")
    origin = models.CharField(max_length=255, blank=True)
    app = models.ForeignKey(GitHubApp, null=True, on_delete=models.SET_NULL)
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
    is_auto = models.BooleanField(default=False)
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
    base_ref = models.CharField(max_length=500, blank=True, default="")
    state = models.CharField(max_length=16, default="open")
    draft = models.BooleanField(default=False)
    merged_at = models.DateTimeField(null=True)
    remote_created_at = models.DateTimeField(null=True)
    remote_closed_at = models.DateTimeField(null=True)
    review_state = models.CharField(max_length=32, default="pending")
    reviewed_at = models.DateTimeField(null=True)
    remote_updated_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mapping", "number"], name="github_mapping_pr_number")]


class GitHubCheckRun(models.Model):
    """A CI check run attached to a pull request, ingested from check_run events."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pull_request = models.ForeignKey(GitHubPullRequest, on_delete=models.CASCADE, related_name="check_runs")
    check_run_id = models.PositiveBigIntegerField()
    name = models.CharField(max_length=255)
    conclusion = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16)
    completed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pull_request", "check_run_id"], name="github_pr_check_run"),
        ]


class GitHubPullRequestReview(models.Model):
    """A review submitted on a pull request, from pull_request_review events."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pull_request = models.ForeignKey(GitHubPullRequest, on_delete=models.CASCADE, related_name="reviews")
    review_id = models.PositiveBigIntegerField()
    user_login = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=32)
    submitted_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pull_request", "review_id"], name="github_pr_review"),
        ]


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


class GitHubCommit(models.Model):
    """A commit in a mapped repository, ingested from push events or search."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mapping = models.ForeignKey(GitHubRepositoryMapping, on_delete=models.CASCADE)
    sha = models.CharField(max_length=64)
    message = models.TextField(blank=True)
    author_name = models.CharField(max_length=255, blank=True)
    author_login = models.CharField(max_length=100, blank=True)
    author_email = models.CharField(max_length=255, blank=True, default="")
    committed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mapping", "sha"], name="github_mapping_commit_sha")]


class GitHubCommitIssueLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE)
    commit = models.ForeignKey(GitHubCommit, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["issue", "commit"], name="github_issue_commit_link")]


class GitHubMentionSearch(models.Model):
    """Cooldown marker for the per-issue GitHub mention search backfill."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, unique=True)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=200, blank=True)


class GitHubAutomationRule(models.Model):
    """Branch-triggered automation for a project's linked work items.

    When a pull request merges into `base_branch` (any branch when blank),
    linked work items move to `target_state` and/or are assigned to `assignee`
    — each action optional, each rule independently toggleable. Rules that
    require every linked pull request to be merged before acting can turn
    that guarantee off.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="github_automation_rules")
    enabled = models.BooleanField(default=True)
    base_branch = models.CharField(max_length=255, blank=True, default="")
    target_state = models.ForeignKey("db.State", on_delete=models.CASCADE, null=True, related_name="github_automation_rules")
    assignee = models.ForeignKey("db.User", on_delete=models.SET_NULL, null=True, related_name="github_automation_rules")
    require_all_merged = models.BooleanField(default=True)
    created_by = models.ForeignKey("db.User", null=True, on_delete=models.SET_NULL, related_name="github_automation_rules_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class GitHubWebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    connection = models.ForeignKey(GitHubConnection, null=True, on_delete=models.SET_NULL)
    app = models.ForeignKey(GitHubApp, null=True, on_delete=models.SET_NULL)
    host = models.CharField(max_length=255, default="github.com")
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
