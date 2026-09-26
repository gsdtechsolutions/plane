# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
from django.urls import path
from . import api

workspace = "workspaces/<str:slug>/github-delivery/"
issue = "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/github-pull-requests/"
urlpatterns = [
    path(workspace, api.ConnectionStatusEndpoint.as_view()),
    path(workspace + "connect/", api.ConnectEndpoint.as_view()),
    path(workspace + "connections/<uuid:connection_id>/", api.DisconnectEndpoint.as_view()),
    path(workspace + "connections/<uuid:connection_id>/repositories/", api.RepositoriesEndpoint.as_view()),
    path(workspace + "mappings/", api.MappingsEndpoint.as_view()),
    path(workspace + "mappings/<uuid:mapping_id>/", api.MappingDetailEndpoint.as_view()),
    path("workspaces/<str:slug>/projects/<uuid:project_id>/github-delivery/", api.ProjectDevelopmentEndpoint.as_view()),
    path(issue, api.IssuePullRequestsEndpoint.as_view()),
    path(issue + "<uuid:pull_request_id>/", api.IssuePullRequestsEndpoint.as_view()),
    path("github-delivery/setup/", api.SetupEndpoint.as_view()),
    path("github-delivery/callback/", api.CallbackEndpoint.as_view()),
    path("github-delivery/webhooks/", api.WebhookEndpoint.as_view()),
    path("github-delivery/manifest/start/", api.ManifestStartEndpoint.as_view()),
    path("github-delivery/manifest/callback/", api.ManifestCallbackEndpoint.as_view()),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/github-delivery/automation/",
        api.ProjectAutomationEndpoint.as_view(),
    ),
    path(workspace + "backfill/", api.BackfillEndpoint.as_view()),
    path(workspace + "health/", api.WebhookHealthEndpoint.as_view()),
]
