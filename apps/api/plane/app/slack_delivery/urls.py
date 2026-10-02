# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
from django.urls import path
from . import api
from .embed import EmbedIssueEndpoint
from .web_dispatch import (
    AgentDispatchCancelEndpoint,
    AgentDispatchEndpoint,
    AgentDispatchMessageEndpoint,
)

workspace = "workspaces/<str:slug>/slack-delivery/"
issue = "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/slack-messages/"
web_dispatch = "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/agent-dispatch/"
urlpatterns = [
    path("slack-delivery/embed/", EmbedIssueEndpoint.as_view()),
    path(workspace, api.ConnectionStatusEndpoint.as_view()),
    path(workspace + "setup/", api.SetupEndpoint.as_view()),
    path(workspace + "connect/", api.ConnectEndpoint.as_view()),
    path(workspace + "connections/<uuid:connection_id>/", api.DisconnectEndpoint.as_view()),
    path(workspace + "connections/<uuid:connection_id>/channels/", api.ChannelsEndpoint.as_view()),
    path(workspace + "mappings/", api.MappingsEndpoint.as_view()),
    path(workspace + "mappings/<uuid:mapping_id>/", api.MappingDetailEndpoint.as_view()),
    path(workspace + "mappings/<uuid:mapping_id>/notify/", api.MappingNotifyEndpoint.as_view()),
    path("workspaces/<str:slug>/projects/<uuid:project_id>/slack-delivery/", api.ProjectConversationsEndpoint.as_view()),
    path(issue, api.IssueMessagesEndpoint.as_view()),
    path(issue + "<uuid:message_id>/", api.IssueMessagesEndpoint.as_view()),
    path("slack-delivery/callback/", api.CallbackEndpoint.as_view()),
    path("slack-delivery/webhooks/", api.WebhookEndpoint.as_view()),
    path("slack-delivery/interactivity/", api.InteractivityEndpoint.as_view()),
    path("slack-delivery/commands/", api.CommandsEndpoint.as_view()),
    path("slack-delivery/agent-events/", api.AgentEventsEndpoint.as_view()),
    path(web_dispatch, AgentDispatchEndpoint.as_view()),
    path(web_dispatch + "<uuid:run_id>/messages/", AgentDispatchMessageEndpoint.as_view()),
    path(web_dispatch + "<uuid:run_id>/cancel/", AgentDispatchCancelEndpoint.as_view()),
]
