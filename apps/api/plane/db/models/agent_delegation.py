# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

from django.db import models


class DelegationRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        CLAIMED = "claimed", "Claimed"
        RUNNING = "running", "Running"
        PR_OPENED = "pr_opened", "PR Opened"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="agent_delegation_runs")
    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="agent_delegation_runs")
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="delegation_runs")
    created_by = models.ForeignKey(
        "db.User", on_delete=models.SET_NULL, null=True, related_name="agent_delegation_runs"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    instructions = models.TextField(blank=True)
    branch = models.CharField(max_length=255, blank=True)
    pr_url = models.URLField(blank=True)
    pr_number = models.PositiveIntegerField(null=True, blank=True)
    result_excerpt = models.TextField(blank=True)
    error = models.TextField(blank=True)
    runner_id = models.CharField(max_length=128, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Agent Delegation Run"
