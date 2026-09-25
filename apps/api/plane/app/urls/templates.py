# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.templates.api import IssueTemplateApplyEndpoint, IssueTemplateViewSet

urlpatterns = [
    # Work item templates: list/create
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issue-templates/",
        IssueTemplateViewSet.as_view({"get": "list", "post": "create"}),
        name="issue-templates",
    ),
    # Work item templates: retrieve/update/delete
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issue-templates/<uuid:template_id>/",
        IssueTemplateViewSet.as_view({"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}),
        name="issue-template",
    ),
    # Work item templates: resolve template into create-form prefill payload
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issue-templates/<uuid:template_id>/apply/",
        IssueTemplateApplyEndpoint.as_view(),
        name="issue-template-apply",
    ),
]
