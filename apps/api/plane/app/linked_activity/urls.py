# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.linked_activity.api import LinkedActivityEndpoint

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/linked-activity/",
        LinkedActivityEndpoint.as_view(),
        name="linked-activity-list",
    ),
]
