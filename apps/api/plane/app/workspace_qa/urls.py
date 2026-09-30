# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.workspace_qa.api import WorkspaceAskEndpoint

urlpatterns = [
    path(
        "workspaces/<str:slug>/ask/",
        WorkspaceAskEndpoint.as_view(),
        name="workspace-ask",
    ),
]
