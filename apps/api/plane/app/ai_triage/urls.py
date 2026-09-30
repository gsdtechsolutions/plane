# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.ai_triage.api import (
    TriageSuggestionAcceptEndpoint,
    TriageSuggestionDismissEndpoint,
    TriageSuggestionsEndpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/triage-suggestions/",
        TriageSuggestionsEndpoint.as_view(),
        name="ai-triage-suggestions",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/triage-suggestions/<uuid:sid>/accept/",
        TriageSuggestionAcceptEndpoint.as_view(),
        name="ai-triage-suggestion-accept",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/triage-suggestions/<uuid:sid>/dismiss/",
        TriageSuggestionDismissEndpoint.as_view(),
        name="ai-triage-suggestion-dismiss",
    ),
]
