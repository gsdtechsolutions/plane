# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.infra.api import (
    InfraConnectionDetailEndpoint,
    InfraConnectionEndpoint,
    InfraConnectionResourcesEndpoint,
    InfraConnectionVerifyEndpoint,
    InfraLinkViewSet,
    InfraStatusEndpoint,
)

urlpatterns = [
    # Workspace infra connections (workspace admin): list/create
    path(
        "workspaces/<str:slug>/infra/connections/",
        InfraConnectionEndpoint.as_view(),
        name="infra-connections",
    ),
    # Workspace infra connections: retrieve/update/delete
    path(
        "workspaces/<str:slug>/infra/connections/<uuid:connection_id>/",
        InfraConnectionDetailEndpoint.as_view(),
        name="infra-connection-detail",
    ),
    # Workspace infra connections: live auth probe
    path(
        "workspaces/<str:slug>/infra/connections/<uuid:connection_id>/verify/",
        InfraConnectionVerifyEndpoint.as_view(),
        name="infra-connection-verify",
    ),
    # Workspace infra connections: resource discovery for the pickers
    path(
        "workspaces/<str:slug>/infra/connections/<uuid:connection_id>/resources/",
        InfraConnectionResourcesEndpoint.as_view(),
        name="infra-connection-resources",
    ),
    # Project infra links (board → external resource): list/create
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/infra/links/",
        InfraLinkViewSet.as_view({"get": "list", "post": "create"}),
        name="project-infra-links",
    ),
    # Project infra links: retrieve/update/delete
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/infra/links/<uuid:link_id>/",
        InfraLinkViewSet.as_view({"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}),
        name="project-infra-link-detail",
    ),
    # Project infra links: combined live status for the board
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/infra/status/",
        InfraStatusEndpoint.as_view(),
        name="project-infra-status",
    ),
]
