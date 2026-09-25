# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models

# Module imports
from plane.license.utils.encryption import decrypt_data, encrypt_data
from .project import ProjectBaseModel
from .workspace import WorkspaceBaseModel


class InfraConnection(WorkspaceBaseModel):
    """
    Fork sidecar model: workspace-level registration of one external
    infrastructure service (a Coolify instance or a Grafana instance).

    Designed per FORK.md as a relational sidecar table — stores fork-specific
    external service configuration without modifying the core Workspace schema.

    Attributes:
        workspace (Workspace): Owning workspace (related_name="infra_connections").
        name (str): Human-readable label, unique per workspace among active rows.
        service (str): "coolify" or "grafana".
        base_url (str): API root, e.g. "https://coolify.example.com" or
            "https://grafana.example.com" (no trailing API path).
        api_token_encrypted (str): Fernet-encrypted API token (via
            plane.license.utils.encryption with settings.SECRET_KEY). Never
            serialized back to clients; the proxy decrypts it per outbound call.
    """

    SERVICE_CHOICES = [
        ("coolify", "Coolify"),
        ("grafana", "Grafana"),
    ]

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="infra_connections")
    name = models.CharField(max_length=255)
    service = models.CharField(max_length=20, choices=SERVICE_CHOICES)
    base_url = models.URLField(max_length=2048)
    api_token_encrypted = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Infra Connection"
        verbose_name_plural = "Infra Connections"
        db_table = "infra_connections"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="infra_connection_unique_name_per_workspace",
            )
        ]

    def set_api_token(self, raw_token):
        self.api_token_encrypted = encrypt_data(raw_token) if raw_token else ""

    def get_api_token(self):
        return decrypt_data(self.api_token_encrypted) if self.api_token_encrypted else ""

    def __str__(self):
        return f"{self.workspace.slug} - {self.name} ({self.service})"


class ProjectInfraLink(ProjectBaseModel):
    """
    Fork sidecar model: ties one project (board) to one external resource —
    a Coolify application or a Grafana dashboard — through an InfraConnection.

    Attributes:
        project (Project): The board the link belongs to (related_name="infra_links").
        connection (InfraConnection): Workspace connection the resource lives on.
        kind (str): "coolify_app" or "grafana_dashboard". Must match the
            connection's service (validated at the serializer).
        external_id (str): Coolify application uuid or Grafana dashboard uid.
            Manual Grafana links (arbitrary URL, no uid) keep this empty and are
            excluded from the uniqueness constraint.
        display_name (str): Cached resource title shown in the UI.
        external_url (str): Deep link to the resource (Grafana /d/<uid>, manual
            URL, or the Coolify application URL when derivable).
        meta (dict): Cached display hints (tags, fqdn, ...), refreshed on read.
    """

    KIND_CHOICES = [
        ("coolify_app", "Coolify application"),
        ("grafana_dashboard", "Grafana dashboard"),
    ]

    project = models.ForeignKey("db.Project", on_delete=models.CASCADE, related_name="infra_links")
    connection = models.ForeignKey(InfraConnection, on_delete=models.CASCADE, related_name="project_links")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    external_id = models.CharField(max_length=255, blank=True, default="")
    display_name = models.CharField(max_length=255, blank=True, default="")
    external_url = models.URLField(max_length=2048, blank=True, default="")
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Project Infra Link"
        verbose_name_plural = "Project Infra Links"
        db_table = "project_infra_links"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["project", "connection", "external_id"],
                condition=~models.Q(external_id=""),
                name="infra_link_unique_external_id_per_project",
            )
        ]

    def __str__(self):
        return f"{self.project.name} - {self.display_name or self.external_id or self.kind}"
