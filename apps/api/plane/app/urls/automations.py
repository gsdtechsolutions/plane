# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.automations.api import AutomationRuleToggleEndpoint, AutomationRuleViewSet

urlpatterns = [
    # Board automation rules: list/create
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/automations/rules/",
        AutomationRuleViewSet.as_view({"get": "list", "post": "create"}),
        name="automation-rules",
    ),
    # Board automation rules: retrieve/update/delete
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/automations/rules/<uuid:rule_id>/",
        AutomationRuleViewSet.as_view({"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}),
        name="automation-rule",
    ),
    # Board automation rules: enable/disable toggle
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/automations/rules/<uuid:rule_id>/toggle/",
        AutomationRuleToggleEndpoint.as_view(),
        name="automation-rule-toggle",
    ),
]
