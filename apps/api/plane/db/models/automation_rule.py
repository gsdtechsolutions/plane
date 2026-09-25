# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
import uuid

from django.db import models

# Module imports
from .project import ProjectBaseModel


class AutomationRule(ProjectBaseModel):
    """
    Fork sidecar model: per-project board automation rule (WHEN trigger THEN actions).

    Designed per FORK.md as a relational sidecar table — stores fork-specific
    automation configuration without modifying the core Project/Issue schema.

    Attributes:
        project (Project): The project the rule belongs to (related_name="automation_rules").
        name (str): Human-readable rule name.
        trigger_type (str): "state_changed" or "assignee_added".
        trigger_value (UUID): Contextual trigger filter — state id for state_changed,
            user id for assignee_added; null means "any".
        actions (list): Ordered list of {"type": ..., "value": ...} dicts applied in
            order when the trigger fires.
        is_active (bool): Per-rule enable/disable switch.
    """

    TRIGGER_TYPE_CHOICES = [
        ("state_changed", "State changed"),
        ("assignee_added", "Assignee added"),
    ]

    ACTION_TYPE_CHOICES = [
        ("set_state", "Set state"),
        ("set_priority", "Set priority"),
        ("add_label", "Add label"),
        ("remove_label", "Remove label"),
        ("assign_member", "Assign member"),
        ("set_due_date", "Set due date"),
    ]

    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="automation_rules",
    )
    name = models.CharField(max_length=255)
    trigger_type = models.CharField(max_length=30, choices=TRIGGER_TYPE_CHOICES)
    trigger_value = models.UUIDField(null=True, blank=True)
    actions = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Automation Rule"
        verbose_name_plural = "Automation Rules"
        db_table = "automation_rules"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.project.name} - {self.name}"


class AutomationExecution(models.Model):
    """Durable, retryable result of one rule matching one committed mutation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_id = models.UUIDField()
    rule = models.ForeignKey(AutomationRule, on_delete=models.CASCADE)
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE)
    status = models.CharField(max_length=16, default="pending")
    error = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "automation_executions"
        constraints = [models.UniqueConstraint(fields=["rule", "event_id"], name="automation_rule_event_unique")]
