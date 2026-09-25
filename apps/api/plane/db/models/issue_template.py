# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models

# Module imports
from .project import ProjectBaseModel


class IssueTemplate(ProjectBaseModel):
    """
    Fork sidecar model: per-project work item template.

    Designed per FORK.md as a relational sidecar table — stores reusable work
    item starting points (name, description, priority, state, labels,
    assignees, relative due date) without modifying the core Issue schema.
    Applying a template prefills the create-work-item form.

    Attributes:
        project (Project): The project the template belongs to (related_name="issue_templates").
        name (str): Human-readable template name.
        description_html (str): Rich text description (same shape as Issue.description_html).
        description_json (dict): Rich text document (same shape as Issue.description_json).
        priority (str): One of the issue priorities ("urgent".."none").
        state (State): Default state for created work items (nullable).
        labels (list[Label]): Default project labels.
        assignees (list[User]): Default assignees.
        due_in_days (int): Template-relative due date; applied as today + due_in_days.
        is_default (bool): At most one default template per project (enforced in save()
            plus a partial unique constraint as a safety net).
    """

    PRIORITY_CHOICES = (
        ("urgent", "Urgent"),
        ("high", "High"),
        ("medium", "Medium"),
        ("low", "Low"),
        ("none", "None"),
    )

    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="issue_templates",
    )
    name = models.CharField(max_length=255)
    description_json = models.JSONField(blank=True, default=dict)
    description_html = models.TextField(blank=True, default="<p></p>")
    priority = models.CharField(
        max_length=30,
        choices=PRIORITY_CHOICES,
        default="none",
    )
    state = models.ForeignKey(
        "db.State",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issue_templates",
    )
    labels = models.ManyToManyField("db.Label", blank=True, related_name="issue_templates")
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="issue_templates",
    )
    due_in_days = models.IntegerField(null=True, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Issue Template"
        verbose_name_plural = "Issue Templates"
        db_table = "issue_templates"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(is_default=True),
                name="issue_template_unique_default_per_project",
            ),
        ]

    def save(self, *args, **kwargs):
        # One default template per project: demote any other default BEFORE saving so the
        # partial unique constraint is never violated (the constraint is the race backstop).
        if self.is_default:
            others = IssueTemplate.objects.filter(project=self.project, is_default=True)
            if not self._state.adding:
                others = others.exclude(pk=self.pk)
            others.update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.project.name} - {self.name}"
