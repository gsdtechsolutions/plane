# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Fork sidecar model: reviewable AI triage suggestions attached to a work item.

The async triage pass (see plane.app.ai_triage.tasks) writes suggestions here;
nothing is ever applied to the issue silently. Every row is a chip a project
member can accept or dismiss from the issue detail view."""

import uuid

from django.db import models


class AIIssueSuggestion(models.Model):
    class Kind(models.TextChoices):
        LABEL = "label", "Label"
        ASSIGNEE = "assignee", "Assignee"
        PRIORITY = "priority", "Priority"
        SUMMARY = "summary", "Summary"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DISMISSED = "dismissed", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="ai_issue_suggestions",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="ai_issue_suggestions",
    )
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="triage_suggestions",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    # Kind-shaped: {"name": ...} for labels, {"user_id", "email"} for assignees,
    # {"priority": ...} for priority, {"summary": ...} for summary.
    payload = models.JSONField(default=dict)
    confidence = models.FloatField(null=True, blank=True)
    model = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(
        "db.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_triage_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "AI Issue Suggestion"
        verbose_name_plural = "AI Issue Suggestions"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.kind} ({self.status}) for issue {self.issue_id}"
