# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
from django.urls import path

from .api import (
    DelegationDetailEndpoint,
    DelegationListEndpoint,
    RunnerClaimEndpoint,
    RunnerEventEndpoint,
    RunnerHealthEndpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/agent-delegations/",
        DelegationListEndpoint.as_view(),
        name="agent-delegation-list",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/agent-delegations/<uuid:run_id>/",
        DelegationDetailEndpoint.as_view(),
        name="agent-delegation-detail",
    ),
    path("agent-runner/claim/", RunnerClaimEndpoint.as_view(), name="agent-runner-claim"),
    path("agent-runner/<uuid:run_id>/events/", RunnerEventEndpoint.as_view(), name="agent-runner-events"),
    path("agent-runner/health/", RunnerHealthEndpoint.as_view(), name="agent-runner-health"),
]
