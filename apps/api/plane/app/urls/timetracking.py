# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.timetracking.api import (
    IssueTimeEntrySummaryEndpoint,
    TimeEntryViewSet,
    TimerStartEndpoint,
    TimerStopEndpoint,
    WorkspaceTimeEntryEndpoint,
)

urlpatterns = [
    # Time entries: issue-scoped list/create
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/time-entries/",
        TimeEntryViewSet.as_view({"get": "list", "post": "create"}),
        name="project-issue-time-entries",
    ),
    # Time entries: rollup summary for one issue
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/time-entries/summary/",
        IssueTimeEntrySummaryEndpoint.as_view(),
        name="project-issue-time-entries-summary",
    ),
    # Time entries: issue-scoped retrieve/update/delete
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/time-entries/<uuid:entry_id>/",
        TimeEntryViewSet.as_view({"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}),
        name="project-issue-time-entry",
    ),
    # Running timer: start (stop-then-start, one per user globally)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/timer/start/",
        TimerStartEndpoint.as_view(),
        name="project-issue-timer-start",
    ),
    # Running timer: stop (body may carry issue_id for a cross-issue stop)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/timer/stop/",
        TimerStopEndpoint.as_view(),
        name="project-issue-timer-stop",
    ),
    # Time entries: workspace-wide paginated list
    path(
        "workspaces/<str:slug>/time-entries/",
        WorkspaceTimeEntryEndpoint.as_view(),
        name="workspace-time-entries",
    ),
]
