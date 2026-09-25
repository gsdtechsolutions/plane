# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.db import models

# Module imports
from .base import BaseModel


class AffineConnection(BaseModel):
    """
    Fork sidecar model: an AFFiNE workspace connected to this Plane workspace's wiki.

    Relational sidecar per FORK.md — keeps fork-specific integration state out of
    the core schema. One active connection per Plane workspace; the connection
    targets one Plane project whose wiki pages mirror the docs of the selected
    AFFiNE workspace (cloud or self-hosted, via the AFFiNE public API).

    Attributes:
        workspace (Workspace): Plane workspace owning this connection.
        project (Project): Plane project whose wiki the AFFiNE docs sync into.
        affine_instance_url (str): Base URL of the AFFiNE instance
            (e.g. https://app.affine.com or https://affine.example.com).
        affine_workspace_id (str): AFFiNE workspace id selected at connect time.
        affine_workspace_name (str): Display name captured at connect time.
        api_token (str): AFFiNE public API bearer token (write-only; never
            serialized back out by the API layer).
        owned_by (User): Connecting user — synced pages are owned by this user.
        settings (dict): {"sync_deletions": bool, "conflict_strategy":
            "newest_wins"|"affine_wins"|"plane_wins", "direction":
            "two_way"|"affine_to_plane"|"plane_to_affine"}.
        last_synced_at / last_sync_status / last_sync_error: last run bookkeeping
            for the settings UI ("never" | "ok" | "error").
        is_active (bool): Pause/resume without deleting the mapping table.
    """

    STATUS_CHOICES = (
        ("never", "Never synced"),
        ("ok", "OK"),
        ("error", "Error"),
    )

    workspace = models.ForeignKey(
        "db.Workspace",
        on_delete=models.CASCADE,
        related_name="affine_connections",
    )
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="affine_connections",
    )
    affine_instance_url = models.URLField(max_length=255)
    affine_workspace_id = models.CharField(max_length=255)
    affine_workspace_name = models.CharField(max_length=255, blank=True, default="")
    api_token = models.TextField()
    owned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="affine_connections",
    )
    settings = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="never")
    last_sync_error = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "AFFiNE Connection"
        verbose_name_plural = "AFFiNE Connections"
        db_table = "affine_connections"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(deleted_at__isnull=True),
                name="affine_connection_unique_per_workspace",
            ),
        ]

    def __str__(self):
        return f"{self.workspace.slug} <- AFFiNE {self.affine_workspace_name or self.affine_workspace_id}"

    @property
    def conflict_strategy(self):
        return (self.settings or {}).get("conflict_strategy", "newest_wins")

    @property
    def direction(self):
        return (self.settings or {}).get("direction", "two_way")

    @property
    def sync_deletions(self):
        return bool((self.settings or {}).get("sync_deletions", False))


class AffinePageMap(BaseModel):
    """
    Fork sidecar model: one synced doc pair (AFFiNE doc <-> Plane wiki page).

    Tracks the content hashes last seen on each side plus the remote
    ``updated_at`` so the sync engine can tell which side changed since the
    last successful sync and detect conflicts when both changed.

    Attributes:
        connection (AffineConnection): Parent connection.
        page (Page): Plane wiki page (CASCADE — deleting the page drops the map).
        affine_doc_id (str): AFFiNE doc GUID.
        affine_doc_title (str): Last known doc title.
        affine_updated_at (datetime): Remote doc updated_at at last sync.
        affine_content_hash (str): sha256 of last-synced AFFiNE markdown.
        plane_content_hash (str): sha256 of last-synced Plane description_html.
        plane_updated_at (datetime): Plane page updated_at at last sync.
        last_synced_at (datetime): When this pair last synced successfully.
        last_sync_direction (str): "pull" | "push" | "create" | "none".
        status (str): "synced" | "conflict" | "error" | "pending".
        last_error (str): Human-readable last per-page error.
    """

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("synced", "Synced"),
        ("conflict", "Conflict"),
        ("error", "Error"),
    )

    DIRECTION_CHOICES = (
        ("pull", "AFFiNE to Plane"),
        ("push", "Plane to AFFiNE"),
        ("create", "Initial create"),
        ("none", "No content change"),
    )

    connection = models.ForeignKey(
        AffineConnection,
        on_delete=models.CASCADE,
        related_name="page_maps",
    )
    page = models.ForeignKey(
        "db.Page",
        on_delete=models.CASCADE,
        related_name="affine_maps",
    )
    affine_doc_id = models.CharField(max_length=255)
    affine_doc_title = models.CharField(max_length=512, blank=True, default="")
    affine_updated_at = models.DateTimeField(null=True, blank=True)
    affine_content_hash = models.CharField(max_length=64, blank=True, default="")
    plane_content_hash = models.CharField(max_length=64, blank=True, default="")
    plane_updated_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default="none")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    last_error = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "AFFiNE Page Map"
        verbose_name_plural = "AFFiNE Page Maps"
        db_table = "affine_page_maps"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "affine_doc_id"],
                condition=models.Q(deleted_at__isnull=True),
                name="affine_pagemap_unique_doc_per_connection",
            ),
            models.UniqueConstraint(
                fields=["connection", "page"],
                condition=models.Q(deleted_at__isnull=True),
                name="affine_pagemap_unique_page_per_connection",
            ),
        ]
        indexes = [
            models.Index(fields=["affine_doc_id"], name="affine_pagemap_doc_idx"),
        ]

    def __str__(self):
        return f"{self.affine_doc_id} <-> page {self.page_id}"
