# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views.affine import (
    AffineConnectionEndpoint,
    AffineProbeEndpoint,
    AffineSyncEndpoint,
    AffineSyncPagesEndpoint,
)

urlpatterns = [
    # AFFiNE probe: validate token + list remote workspaces (pre-connect)
    path(
        "workspaces/<str:slug>/affine/probe/",
        AffineProbeEndpoint.as_view(),
        name="affine-probe",
    ),
    # AFFiNE connection: retrieve/create/update/delete the workspace connection
    path(
        "workspaces/<str:slug>/affine/connection/",
        AffineConnectionEndpoint.as_view(),
        name="affine-connection",
    ),
    # AFFiNE sync: trigger a two-way sync pass now
    path(
        "workspaces/<str:slug>/affine/connection/sync/",
        AffineSyncEndpoint.as_view(),
        name="affine-sync",
    ),
    # AFFiNE sync pages: mapped page pairs with sync statuses
    path(
        "workspaces/<str:slug>/affine/connection/pages/",
        AffineSyncPagesEndpoint.as_view(),
        name="affine-sync-pages",
    ),
]
