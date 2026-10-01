# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
import uuid

from django.db import models

# Module imports
from .project import ProjectBaseModel
from .workspace import WorkspaceBaseModel


class AsanaConnection(WorkspaceBaseModel):
    """
    Fork sidecar model: workspace-level Asana connection (personal access token).

    Designed per FORK.md as a relational sidecar table — the token is stored
    encrypted at rest (Fernet, key derived from SECRET_KEY) and is never
    returned by any serializer.

    Attributes:
        workspace (Workspace): Owning workspace (related_name="asana_connections").
        name (str): Human-readable label for the connection.
        pat_encrypted (str): Fernet-encrypted Asana Personal Access Token.
        asana_workspace_gid (str): GID of the Asana workspace/org the token belongs to.
        asana_workspace_name (str): Display name of the Asana workspace.
        is_active (bool): Soft kill-switch for every sync under this connection.
        last_verified_at (datetime): Last time the token was verified against /users/me.
    """

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="asana_connections",
    )
    name = models.CharField(max_length=255)
    pat_encrypted = models.TextField()
    asana_workspace_gid = models.CharField(max_length=64, blank=True, default="")
    asana_workspace_name = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Asana Connection"
        verbose_name_plural = "Asana Connections"
        db_table = "asana_connections"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="asana_connection_unique_name_workspace_when_deleted_at_null",
            ),
        ]

    def __str__(self):
        return f"{self.workspace.slug} - {self.name}"


class AsanaProjectSync(ProjectBaseModel):
    """
    Fork sidecar model: bidirectional sync configuration between one Asana
    project and one Plane project.

    Attributes:
        project (Project): The Plane project being synced (related_name="asana_syncs").
        connection (AsanaConnection): Workspace-level credential holder.
        asana_project_gid (str): GID of the remote Asana project.
        asana_project_name (str): Display name captured at map time.
        direction (str): "pull", "push" or "bidirectional".
        sync_subtasks (bool): Mirror Asana subtasks as sub-issues (and back).
        sync_comments (bool): Mirror task stories (comments) as issue comments (and back).
        state_map (dict): Asana section gid -> {"state_id": uuid, "group": str} fallback
            used when a task has no section or an unmapped section.
        default_state_id (uuid): State used for tasks without a mapped section.
        label_map (dict): Asana tag gid -> Plane label id.
        assignee_map (dict): Asana user gid -> Plane member id.
        webhook_configured (bool): An Asana webhook points at this sync.
        webhook_gid (str): Asana webhook resource gid (used to delete it).
        webhook_secret (str): X-Hook-Secret returned by Asana's handshake, used to
            HMAC-verify subsequent event deliveries.
        initial_sync_done (bool): First full pass completed; later passes are delta runs.
        last_synced_at (datetime): Start timestamp of the last completed engine pass.
        is_active (bool): Per-sync kill-switch.
    """

    DIRECTION_CHOICES = [
        ("pull", "Pull from Asana"),
        ("push", "Push to Asana"),
        ("bidirectional", "Bidirectional"),
    ]

    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="asana_syncs",
    )
    connection = models.ForeignKey(
        AsanaConnection,
        on_delete=models.CASCADE,
        related_name="project_syncs",
    )
    asana_project_gid = models.CharField(max_length=64)
    asana_project_name = models.CharField(max_length=255, blank=True, default="")
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES, default="bidirectional")
    sync_subtasks = models.BooleanField(default=True)
    sync_comments = models.BooleanField(default=True)
    state_map = models.JSONField(default=dict)
    default_state_id = models.UUIDField(null=True, blank=True)
    label_map = models.JSONField(default=dict)
    assignee_map = models.JSONField(default=dict)
    webhook_configured = models.BooleanField(default=False)
    webhook_gid = models.CharField(max_length=64, blank=True, default="")
    webhook_secret = models.TextField(blank=True, default="")
    initial_sync_done = models.BooleanField(default=False)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # Custom property (select, people options) that mirrors the Asana assignee
    # so issues can be assigned to anyone — Plane members and Asana-only
    # people — and stay in sync with Asana in both directions.
    assignee_property_id = models.UUIDField(null=True, blank=True)

    class Meta:
        verbose_name = "Asana Project Sync"
        verbose_name_plural = "Asana Project Syncs"
        db_table = "asana_project_syncs"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "asana_project_gid"],
                condition=models.Q(deleted_at__isnull=True),
                name="asana_sync_unique_project_gid_when_deleted_at_null",
            ),
        ]

    def __str__(self):
        return f"{self.project.name} <-> {self.asana_project_name or self.asana_project_gid}"


class AsanaTaskLink(ProjectBaseModel):
    """
    Fork sidecar model: identity link between one Asana task and one Plane issue
    within a sync. Caches both sides' modification timestamps so the engine can
    decide the winning side (last-write-wins) without re-fetching history.
    """

    sync = models.ForeignKey(
        AsanaProjectSync,
        on_delete=models.CASCADE,
        related_name="task_links",
    )
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="asana_task_links",
    )
    asana_task_gid = models.CharField(max_length=64)
    asana_modified_at = models.DateTimeField(null=True, blank=True)
    plane_synced_at = models.DateTimeField(null=True, blank=True)
    asana_name_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        verbose_name = "Asana Task Link"
        verbose_name_plural = "Asana Task Links"
        db_table = "asana_task_links"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["sync", "issue"],
                condition=models.Q(deleted_at__isnull=True),
                name="asana_task_link_unique_issue_when_deleted_at_null",
            ),
            models.UniqueConstraint(
                fields=["sync", "asana_task_gid"],
                condition=models.Q(deleted_at__isnull=True),
                name="asana_task_link_unique_task_when_deleted_at_null",
            ),
        ]

    def __str__(self):
        return f"{self.asana_task_gid} -> {self.issue_id}"


class AsanaCommentLink(ProjectBaseModel):
    """Fork sidecar model: identity link between one Asana story and one Plane comment."""

    task_link = models.ForeignKey(
        AsanaTaskLink,
        on_delete=models.CASCADE,
        related_name="comment_links",
    )
    issue_comment = models.ForeignKey(
        "db.IssueComment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="asana_comment_links",
    )
    asana_story_gid = models.CharField(max_length=64)
    direction = models.CharField(max_length=10, default="pull")  # pull | push

    class Meta:
        verbose_name = "Asana Comment Link"
        verbose_name_plural = "Asana Comment Links"
        db_table = "asana_comment_links"
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["task_link", "asana_story_gid"],
                condition=models.Q(deleted_at__isnull=True),
                name="asana_comment_link_unique_story_when_deleted_at_null",
            ),
        ]

    def __str__(self):
        return f"{self.asana_story_gid} -> {self.issue_comment_id}"


class AsanaSyncLog(ProjectBaseModel):
    """
    Fork sidecar model: durable audit trail for the sync engine. One row per
    remote or local entity attempt, so the settings UI can show what happened.
    """

    STATUS_CHOICES = [
        ("success", "Success"),
        ("error", "Error"),
        ("skipped", "Skipped"),
        ("conflict", "Conflict resolved"),
    ]

    sync = models.ForeignKey(
        AsanaProjectSync,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="logs",
    )
    direction = models.CharField(max_length=10, default="pull")  # pull | push
    entity_type = models.CharField(max_length=20)  # task | subtask | comment | project
    entity_gid = models.CharField(max_length=64, blank=True, default="")
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asana_sync_logs",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="success")
    message = models.CharField(max_length=512, blank=True, default="")
    detail = models.JSONField(default=dict, null=True, blank=True)

    class Meta:
        verbose_name = "Asana Sync Log"
        verbose_name_plural = "Asana Sync Logs"
        db_table = "asana_sync_logs"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["sync", "-created_at"], name="asana_sync_log_sync_created"),
        ]

    def __str__(self):
        return f"{self.direction}:{self.entity_type}:{self.status}"
