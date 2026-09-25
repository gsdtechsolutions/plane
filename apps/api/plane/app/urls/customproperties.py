# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.customproperties.api import (
    CustomPropertyReorderEndpoint,
    CustomPropertyViewSet,
    IssueCustomPropertyValuesEndpoint,
)

urlpatterns = [
    # Custom property definitions: list/create
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/custom-properties/",
        CustomPropertyViewSet.as_view({"get": "list", "post": "create"}),
        name="custom-properties",
    ),
    # Custom property definitions: retrieve/update/delete
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/custom-properties/<uuid:property_id>/",
        CustomPropertyViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}
        ),
        name="custom-property",
    ),
    # Custom property definitions: reorder (must precede the <uuid:property_id> route)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/custom-properties/reorder/",
        CustomPropertyReorderEndpoint.as_view(),
        name="custom-properties-reorder",
    ),
    # Issue values: read the full typed map / full replace
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/custom-property-values/",
        IssueCustomPropertyValuesEndpoint.as_view(),
        name="issue-custom-property-values",
    ),
]
