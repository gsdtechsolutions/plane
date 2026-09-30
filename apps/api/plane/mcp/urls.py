# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.mcp.views import MCPEndpoint

urlpatterns = [
    path("", MCPEndpoint.as_view(http_method_names=["post", "get", "options"]), name="mcp-endpoint"),
]
