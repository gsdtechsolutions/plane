# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.ai_ops.api import AIActionAuditListEndpoint
from plane.app.ai_ops.configuration import AIOperationsConfigEndpoint

urlpatterns = [
    path(
        "workspaces/<str:slug>/ai-audit/",
        AIActionAuditListEndpoint.as_view(),
        name="ai-audit-list",
    ),
    path(
        "workspaces/<str:slug>/ai-configuration/",
        AIOperationsConfigEndpoint.as_view(),
        name="ai-configuration",
    ),
]
