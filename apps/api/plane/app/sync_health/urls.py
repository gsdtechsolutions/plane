# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.sync_health.api import SyncHealthEndpoint

urlpatterns = [
    path(
        "workspaces/<str:slug>/integrations/sync-health/",
        SyncHealthEndpoint.as_view(),
        name="sync-health",
    ),
]
