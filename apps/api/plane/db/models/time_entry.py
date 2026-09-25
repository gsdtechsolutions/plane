# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models
from django.db.models import Q

# Module imports
from .project import ProjectBaseModel


class TimeEntry(ProjectBaseModel):
    """
    Fork sidecar model: time logged against an issue.

    Doubles as the storage for the per-user running timer: an entry with
    started_at set and ended_at null is an open timer. At most one open
    timer may exist per user across the instance (stop-then-start
    semantics enforced by the timer endpoints).

    Attributes:
        project (Project): The project the entry belongs to (related_name="time_entries").
        issue (Issue): The issue the time was logged against (related_name="time_entries").
        user (User): The member who logged the time (related_name="time_entries").
        minutes (int): Whole minutes of logged time. Never negative.
        started_at (datetime): When the logged period started; also marks an
            open timer when set with ended_at null.
        ended_at (datetime): When the logged period ended; null while a timer
            is running.
        description (str): Free-form note about what the time was spent on.
    """

    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="time_entries",
    )
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="time_entries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="time_entries",
    )
    minutes = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Time Entry"
        verbose_name_plural = "Time Entries"
        db_table = "time_entries"
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=Q(minutes__gte=0), name="time_entry_minutes_non_negative"),
        ]

    def __str__(self):
        return f"{self.issue_id} {self.user_id} {self.minutes}m"
