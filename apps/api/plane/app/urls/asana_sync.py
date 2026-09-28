# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.asana_sync.api import (
    AsanaConnectionVerifyEndpoint,
    AsanaConnectionViewSet,
    AsanaProjectSyncViewSet,
    AsanaRemoteBrowseEndpoint,
    AsanaSyncAssigneesEndpoint,
    AsanaSyncLogsEndpoint,
    AsanaSyncRunEndpoint,
    AsanaWorkspaceSyncAssigneesEndpoint,
    AsanaWorkspaceSyncListEndpoint,
    AsanaWorkspaceSyncLogsEndpoint,
    AsanaWorkspaceSyncRunEndpoint,
    AsanaSyncWebhookEndpoint,
)
from plane.app.asana_sync.webhook import AsanaWebhookEndpoint

urlpatterns = [
    # Workspace-level connections (workspace admins)
    path(
        "workspaces/<str:slug>/asana-sync/connections/",
        AsanaConnectionViewSet.as_view({"get": "list", "post": "create"}),
        name="asana-connections",
    ),
    path(
        "workspaces/<str:slug>/asana-sync/connections/<uuid:connection_id>/",
        AsanaConnectionViewSet.as_view({"get": "retrieve", "delete": "destroy", "patch": "partial_update"}),
        name="asana-connection",
    ),
    path(
        "workspaces/<str:slug>/asana-sync/connections/<uuid:connection_id>/verify/",
        AsanaConnectionVerifyEndpoint.as_view(),
        name="asana-connection-verify",
    ),
    path(
        "workspaces/<str:slug>/asana-sync/connections/<uuid:connection_id>/remote/",
        AsanaRemoteBrowseEndpoint.as_view(),
        name="asana-remote-browse",
    ),
    # Workspace-level sync management (workspace admins / project admins)
    path(
        "workspaces/<str:slug>/asana-sync/syncs/",
        AsanaWorkspaceSyncListEndpoint.as_view(),
        name="asana-workspace-syncs",
    ),
    path(
        "workspaces/<str:slug>/asana-sync/syncs/<uuid:sync_id>/run/",
        AsanaWorkspaceSyncRunEndpoint.as_view(),
        name="asana-workspace-sync-run",
    ),
    path(
        "workspaces/<str:slug>/asana-sync/syncs/<uuid:sync_id>/logs/",
        AsanaWorkspaceSyncLogsEndpoint.as_view(),
        name="asana-workspace-sync-logs",
    ),
    path(
        "workspaces/<str:slug>/asana-sync/syncs/<uuid:sync_id>/assignees/",
        AsanaWorkspaceSyncAssigneesEndpoint.as_view(),
        name="asana-workspace-sync-assignees",
    ),
    # Per-project sync mappings (project admins)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/asana-sync/",
        AsanaProjectSyncViewSet.as_view({"get": "list", "post": "create"}),
        name="asana-project-syncs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/asana-sync/<uuid:sync_id>/",
        AsanaProjectSyncViewSet.as_view({"get": "retrieve", "delete": "destroy", "patch": "partial_update"}),
        name="asana-project-sync",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/asana-sync/<uuid:sync_id>/webhook/",
        AsanaSyncWebhookEndpoint.as_view(),
        name="asana-sync-webhook",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/asana-sync/<uuid:sync_id>/run/",
        AsanaSyncRunEndpoint.as_view(),
        name="asana-sync-run",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/asana-sync/<uuid:sync_id>/logs/",
        AsanaSyncLogsEndpoint.as_view(),
        name="asana-sync-logs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/asana-sync/<uuid:sync_id>/assignees/",
        AsanaSyncAssigneesEndpoint.as_view(),
        name="asana-sync-assignees",
    ),
    # Public webhook receiver (HMAC-verified)
    path(
        "asana-sync/webhook/<uuid:sync_id>/",
        AsanaWebhookEndpoint.as_view(),
        name="asana-webhook",
    ),
]
