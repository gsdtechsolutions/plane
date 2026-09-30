# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Fork sidecar model: durable audit trail for AI actions (summaries, triage
suggestions, Slack Asks, agent delegations, workspace Q&A). Every AI feature
records what ran, for whom, and with what outcome so operators can answer
"what did the AI do?" — the governance expectation set by every major platform."""

import uuid

from django.db import models


class AIActionAudit(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="ai_action_audits",
    )
    project = models.ForeignKey(
        "db.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_action_audits",
    )
    actor = models.ForeignKey(
        "db.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_action_audits",
    )
    # Namespaced verb, e.g. "issue.summarize", "issue.triage_suggest",
    # "slack.asks_draft", "slack.ask", "workspace.ask", "agent.delegate".
    action = models.CharField(max_length=64, db_index=True)
    # Free-form entity reference, e.g. ("issue", <uuid>) or ("slack_thread", <channel:ts>).
    entity_type = models.CharField(max_length=32, blank=True)
    entity_id = models.CharField(max_length=128, blank=True)
    # Provider/model that served the call, e.g. "openai/deepseek-chat". Blank
    # for non-LLM actions (agent bookkeeping, suggestion acceptance).
    model = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUCCESS)
    input_excerpt = models.TextField(blank=True)
    output_excerpt = models.TextField(blank=True)
    error = models.TextField(blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "AI Action Audit"
        verbose_name_plural = "AI Action Audits"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["workspace", "created_at"]),
            models.Index(fields=["entity_type", "entity_id"]),
        ]

    def __str__(self):
        return f"{self.action} ({self.status}) @ {self.created_at:%Y-%m-%d %H:%M}"
