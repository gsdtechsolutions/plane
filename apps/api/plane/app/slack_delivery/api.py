# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.db.models import Workspace, WorkspaceMember, Project, ProjectMember, Issue
from plane.db.models.slack_delivery import (
    SlackConnection,
    SlackConnectNonce,
    SlackChannelMapping,
    SlackMessage,
    SlackIssueLink,
    SlackEventDelivery,
    SlackAppSetup,
)
from .client import (
    SlackClient,
    SlackUnavailable,
    app_manifest,
    app_setup_row,
    board_origin,
    configuration,
    encrypt_secret,
    encrypt_token,
    setup_link,
    setup_state,
    require_configuration,
    verify_signature,
)
from . import commands
from . import services
from .tasks import process_slack_event, run_slack_command, sync_slack_mapping


logger = logging.getLogger(__name__)


def workspace_admin(user, slug):
    workspace = get_object_or_404(Workspace, slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=workspace, member=user, role=20, is_active=True).exists()
    ):
        raise PermissionDenied("Workspace administrators manage Slack connections.")
    return workspace


def project_member(user, slug, project_id, *, admin=False):
    project = get_object_or_404(Project, id=project_id, workspace__slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=project.workspace, member=user, is_active=True).exists()
    ):
        raise PermissionDenied()
    members = ProjectMember.objects.filter(project=project, member=user, is_active=True, role__gte=15)
    if not members.exists() or admin and not members.filter(role=20).exists():
        raise PermissionDenied("An active project membership is required.")
    return project


def uuid_input(value):
    return serializers.UUIDField().run_validation(value)


def redirect_to(url):
    response = HttpResponseRedirect(url)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def nonce_for_update(user, state, stage):
    if not isinstance(state, str) or not 32 <= len(state) <= 100:
        raise ValidationError("Connection request is invalid or expired. Start again.")
    nonce = get_object_or_404(
        SlackConnectNonce.objects.select_for_update(),
        token_hash=hashlib.sha256(state.encode()).hexdigest(),
        user=user,
        stage=stage,
        consumed_at__isnull=True,
        expires_at__gt=timezone.now(),
    )
    workspace_admin(user, nonce.workspace.slug)
    return nonce


def connection_data(connection):
    return {
        "id": str(connection.id),
        "team_id": connection.team_id,
        "team_name": connection.team_name,
        "active": connection.is_active,
    }


def mapping_data(mapping):
    return {
        "id": str(mapping.id),
        "connection_id": str(mapping.connection_id),
        "project_id": str(mapping.project_id),
        "channel_id": mapping.channel_id,
        "channel": mapping.channel_name,
        "private": mapping.is_private,
        "active": mapping.is_active,
        "sync_status": mapping.sync_status,
        "sync_error": mapping.sync_error,
        "last_synced_at": services.iso(mapping.last_synced_at),
    }


class ConnectionStatusEndpoint(BaseAPIView):
    def get(self, request, slug):
        workspace = workspace_admin(request.user, slug)
        state = setup_state()
        state["connections"] = [
            connection_data(value) for value in SlackConnection.objects.filter(workspace=workspace)
        ]
        state["mappings"] = [
            mapping_data(value)
            for value in SlackChannelMapping.objects.filter(connection__workspace=workspace, is_active=True)
        ]
        return Response(state)


class SetupEndpoint(BaseAPIView):
    """Click-to-connect app setup: read the manifest link, store admin-entered secrets."""

    def payload(self, request):
        state = setup_state()
        origin = board_origin(request)
        if origin:
            # Fresh URLs from the live request so the app can always be re-created.
            state["manifest"] = app_manifest(origin)
            state["setup_url"] = setup_link(origin)
            state["commands_url"] = f"{origin}/api/slack-delivery/commands/"
        else:
            state["manifest"] = None
            state["setup_url"] = None
            state["configuration_error"] = "The public address of this board could not be determined."
        return state

    def get(self, request, slug):
        workspace_admin(request.user, slug)
        return Response(self.payload(request))

    @transaction.atomic
    def put(self, request, slug):
        workspace_admin(request.user, slug)
        client_id = request.data.get("client_id")
        if not isinstance(client_id, str) or not re.fullmatch(r"[0-9]{6,20}\.[0-9]{6,20}", client_id):
            raise ValidationError("Enter the Client ID shown on the Slack app's Basic Information page.")
        client_secret = request.data.get("client_secret")
        if not isinstance(client_secret, str) or not 10 <= len(client_secret) <= 200:
            raise ValidationError("Enter the Client Secret shown on the Slack app's Basic Information page.")
        signing_secret = request.data.get("signing_secret")
        if not isinstance(signing_secret, str) or not 10 <= len(signing_secret) <= 200:
            raise ValidationError("Enter the Signing Secret shown on the Slack app's Basic Information page.")
        app_id = request.data.get("app_id") or ""
        if not isinstance(app_id, str) or (app_id and not re.fullmatch(r"A[A-Z0-9]{5,20}", app_id)):
            raise ValidationError("Enter the App ID shown on the Slack app's Basic Information page.")
        values = {
            "client_id": client_id,
            "client_secret_encrypted": encrypt_secret(client_secret),
            "signing_secret_encrypted": encrypt_secret(signing_secret),
            "app_id": app_id,
        }
        setup = app_setup_row(lock=True)
        if setup is None:
            SlackAppSetup.objects.create(created_by=request.user, **values)
            status = 201
        else:
            for field, value in values.items():
                setattr(setup, field, value)
            setup.save()
            status = 200
        return Response(self.payload(request), status=status)


class ConnectEndpoint(BaseAPIView):
    def post(self, request, slug):
        workspace = workspace_admin(request.user, slug)
        client = SlackClient()
        state = secrets.token_urlsafe(32)
        SlackConnectNonce.objects.create(
            token_hash=hashlib.sha256(state.encode()).hexdigest(),
            workspace=workspace,
            user=request.user,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        return Response({"url": client.authorization_url(state)})


class CallbackEndpoint(BaseAPIView):
    def get(self, request):
        client = SlackClient()
        code = request.query_params.get("code")
        if not isinstance(code, str) or not 1 <= len(code) <= 1000:
            raise ValidationError("Slack authorization did not complete. Start again.")
        with transaction.atomic():
            nonce = nonce_for_update(request.user, request.query_params.get("state"), "authorization")
            nonce.consumed_at = timezone.now()
            nonce.save(update_fields=["consumed_at"])
        result, token, team, authed_user = client.complete_installation(code)
        team_id = services.slack_id(team.get("id"))
        team_name = services.text(team.get("name"), 255)
        if not team_name:
            raise ValidationError("Slack workspace name is missing.")
        slack_user_id = services.slack_id(authed_user.get("id"))
        bot_user_id = result.get("bot_user_id")
        if not isinstance(bot_user_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{5,30}", bot_user_id):
            raise ValidationError("Slack did not return the app bot for this workspace.")
        # team.info needs team:read; apps installed without that scope still
        # connect — only manual permalink matching depends on the domain.
        try:
            domain = client.team_domain(token)
        except SlackUnavailable:
            logger.warning("slack team.info unavailable (team:read scope missing?); connecting without domain")
            domain = ""
        encrypted = encrypt_token(token)
        with transaction.atomic():
            # Recheck membership after Slack network requests.
            workspace_admin(request.user, nonce.workspace.slug)
            connection, _ = SlackConnection.objects.select_for_update().get_or_create(
                team_id=team_id,
                defaults={
                    "workspace": nonce.workspace,
                    "team_name": team_name,
                    "slack_user_id": slack_user_id,
                    "bot_user_id": bot_user_id,
                    "connected_by": request.user,
                },
            )
            if connection.workspace_id != nonce.workspace_id:
                raise PermissionDenied("This Slack workspace is already connected to another workspace.")
            connection.team_name = team_name
            connection.team_domain = domain
            connection.slack_user_id = slack_user_id
            connection.bot_user_id = bot_user_id
            connection.bot_token_encrypted = encrypted
            connection.connected_by = request.user
            connection.is_active = True
            connection.save()
        return redirect_to(f"/{nonce.workspace.slug}/settings/integrations/?slack=connected")


class DisconnectEndpoint(BaseAPIView):
    @transaction.atomic
    def delete(self, request, slug, connection_id):
        workspace = workspace_admin(request.user, slug)
        connection = get_object_or_404(
            SlackConnection.objects.select_for_update(), id=connection_id, workspace=workspace
        )
        connection.is_active = False
        connection.bot_token_encrypted = ""
        connection.save(update_fields=["is_active", "bot_token_encrypted", "updated_at"])
        SlackChannelMapping.objects.filter(connection=connection).update(is_active=False)
        return Response(status=204)


class ChannelsEndpoint(BaseAPIView):
    def get(self, request, slug, connection_id):
        from .client import bot_token

        workspace = workspace_admin(request.user, slug)
        connection = get_object_or_404(SlackConnection, id=connection_id, workspace=workspace, is_active=True)
        return Response(
            [
                {
                    "id": services.slack_id(channel.get("id")),
                    "name": services.channel_name(channel.get("name")),
                    "private": channel.get("is_private") is True,
                    "member": channel.get("is_member") is True,
                }
                for channel in SlackClient().channels(bot_token(connection))
            ]
        )


class MappingsEndpoint(BaseAPIView):
    def post(self, request, slug):
        from .client import bot_token

        workspace = workspace_admin(request.user, slug)
        project = project_member(request.user, slug, uuid_input(request.data.get("project_id")))
        connection = get_object_or_404(
            SlackConnection, id=uuid_input(request.data.get("connection_id")), workspace=workspace, is_active=True
        )
        channel_id = request.data.get("channel_id")
        if not isinstance(channel_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{5,30}", channel_id):
            raise ValidationError("Enter a Slack channel from this connection.")
        channels = SlackClient().channels(bot_token(connection))
        channel = next((item for item in channels if item.get("id") == channel_id), None)
        if not channel:
            raise PermissionDenied("Select a channel visible to this app.")
        if channel.get("is_member") is not True:
            raise PermissionDenied("Invite this Slack app to the channel before connecting it. The app never joins on its own.")
        name = services.channel_name(channel.get("name"))
        with transaction.atomic():
            connection = SlackConnection.objects.select_for_update().get(pk=connection.pk)
            if not connection.is_active:
                raise PermissionDenied("This Slack connection changed. Reload and try again.")
            workspace_admin(request.user, slug)
            project_member(request.user, slug, project.id)
            existing = SlackChannelMapping.objects.filter(
                connection=connection, channel_id=channel_id, is_active=True
            ).first()
            if existing:
                if existing.project_id != project.id:
                    raise ValidationError(
                        "This channel is mapped to another project. Disconnect that mapping first."
                    )
                return Response(mapping_data(existing))
            mapping = SlackChannelMapping.objects.create(
                connection=connection,
                project=project,
                channel_id=channel_id,
                channel_name=name,
                is_private=channel.get("is_private") is True,
            )
            transaction.on_commit(lambda: sync_slack_mapping.delay(str(mapping.id)), robust=True)
        return Response(mapping_data(mapping), status=201)


class MappingDetailEndpoint(BaseAPIView):
    @transaction.atomic
    def delete(self, request, slug, mapping_id):
        workspace = workspace_admin(request.user, slug)
        mapping = get_object_or_404(
            SlackChannelMapping.objects.select_for_update(), id=mapping_id, connection__workspace=workspace
        )
        mapping.is_active = False
        mapping.save(update_fields=["is_active"])
        return Response(status=204)

    def post(self, request, slug, mapping_id):
        workspace = workspace_admin(request.user, slug)
        mapping = get_object_or_404(
            SlackChannelMapping,
            id=mapping_id,
            connection__workspace=workspace,
            is_active=True,
            connection__is_active=True,
        )
        mapping.sync_status = "pending"
        mapping.sync_error = ""
        mapping.save(update_fields=["sync_status", "sync_error"])
        sync_slack_mapping.delay(str(mapping.id))
        return Response(mapping_data(mapping), status=202)


class ProjectConversationsEndpoint(BaseAPIView):
    def get(self, request, slug, project_id):
        project = project_member(request.user, slug, project_id)
        mappings = SlackChannelMapping.objects.filter(
            project=project, connection__workspace_id=project.workspace_id
        ).select_related("connection")
        return Response(
            {
                "channels": [mapping_data(value) for value in mappings],
                "messages": services.list_project_messages(project),
            }
        )


class IssueMessagesEndpoint(BaseAPIView):
    def issue(self, request, slug, project_id, issue_id):
        project = project_member(request.user, slug, project_id)
        return get_object_or_404(Issue, id=issue_id, project=project, workspace_id=project.workspace_id)

    def get(self, request, slug, project_id, issue_id):
        return Response(services.list_issue_messages(self.issue(request, slug, project_id, issue_id)))

    def post(self, request, slug, project_id, issue_id):
        from .client import bot_token

        issue = self.issue(request, slug, project_id, issue_id)
        if request.data.get("message_id"):
            message = get_object_or_404(
                SlackMessage,
                id=uuid_input(request.data["message_id"]),
                mapping__project_id=issue.project_id,
                mapping__connection__workspace_id=issue.workspace_id,
            )
        else:
            url = request.data.get("url")
            if not isinstance(url, str):
                raise ValidationError("Enter a Slack message permalink from a connected channel in this project.")
            match = None
            connection = None
            for candidate in SlackConnection.objects.filter(workspace_id=issue.workspace_id, is_active=True):
                if not candidate.team_domain:
                    continue
                found = re.fullmatch(
                    rf"https://{re.escape(candidate.team_domain)}\.slack\.com/archives/([A-Z][A-Z0-9]{{5,30}})/p([0-9]{{10}})([0-9]{{1,10}})/?",
                    url,
                )
                if found:
                    match, connection = found, candidate
                    break
            if not match:
                raise ValidationError("Enter a Slack permalink from a channel connected to this project.")
            ts = f"{match[2]}.{match[3]}" if match[3] else match[2]
            mapping = get_object_or_404(
                SlackChannelMapping,
                project_id=issue.project_id,
                connection__workspace_id=issue.workspace_id,
                channel_id=match[1],
                is_active=True,
                connection__is_active=True,
            )
            data = SlackClient().fetch_message(bot_token(mapping.connection), mapping.channel_id, ts)
            # conversations.replies responses omit the channel.
            data = {**data, "channel": mapping.channel_id} if isinstance(data, dict) else data
            if data.get("ts") != ts:
                raise ValidationError("Slack returned a different message.")
            with transaction.atomic():
                connection = SlackConnection.objects.select_for_update().get(id=mapping.connection_id)
                mapping = (
                    SlackChannelMapping.objects.select_for_update()
                    .select_related("connection", "project")
                    .get(id=mapping.id)
                )
                if not connection.is_active or not mapping.is_active:
                    raise PermissionDenied("This channel is disconnected.")
                message = services.upsert_message(mapping, data)
        SlackIssueLink.objects.update_or_create(
            issue=issue, message=message, defaults={"is_manual": True, "is_suppressed": False}
        )
        return Response(services.list_issue_messages(issue), status=201)

    def delete(self, request, slug, project_id, issue_id, message_id):
        issue = self.issue(request, slug, project_id, issue_id)
        message = get_object_or_404(
            SlackMessage,
            id=message_id,
            mapping__project_id=issue.project_id,
            mapping__connection__workspace_id=issue.workspace_id,
        )
        SlackIssueLink.objects.update_or_create(
            issue=issue, message=message, defaults={"is_manual": False, "is_suppressed": True}
        )
        return Response(status=204)


class WebhookEndpoint(BaseAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        secret = configuration()["SIGNING_SECRET"]
        if not secret:
            return Response({"error": "Slack events are not configured."}, status=503)
        try:
            content_length = int(request.META.get("CONTENT_LENGTH", "0") or 0)
        except ValueError:
            raise ValidationError("Invalid request length.")
        if content_length > 1048576:
            return Response({"error": "Event is too large."}, status=413)
        raw = request.body
        if len(raw) > 1048576:
            return Response({"error": "Event is too large."}, status=413)
        if not verify_signature(secret, request.headers.get("X-Slack-Request-Timestamp"), raw, request.headers.get("X-Slack-Signature")):
            raise PermissionDenied("Invalid Slack signature.")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValidationError("Invalid Slack event.")
        # Configuration-time handshake: echo the challenge once, signed.
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not 1 <= len(challenge) <= 256:
                raise ValidationError("Invalid Slack challenge.")
            return Response({"challenge": challenge})
        if payload.get("type") != "event_callback":
            return Response({"status": "ignored"}, status=202)
        event_id = request.headers.get("X-Slack-Event-Id") or payload.get("event_id")
        if not isinstance(event_id, str) or not 4 <= len(event_id) <= 64:
            raise ValidationError("Invalid Slack event id.")
        event = payload.get("event")
        if not isinstance(event, dict) or event.get("type") not in {
            "message",
            "channel_rename",
            "link_shared",
            "app_uninstalled",
            "tokens_revoked",
        }:
            return Response({"status": "ignored"}, status=202)
        team_id = services.slack_id(payload.get("team_id"))
        channel_id = (
            event.get("channel") if event.get("type") in ("message", "link_shared") else None
        )
        if channel_id is not None:
            services.slack_id(channel_id)
        body_hash = hashlib.sha256(raw).hexdigest()
        with transaction.atomic():
            connection = SlackConnection.objects.filter(team_id=team_id).first()
            waiting = connection is None and event.get("type") == "message"
            active = connection is not None and connection.is_active
            existing = SlackEventDelivery.objects.filter(id=event_id).first()
            if waiting and not existing:
                retained = SlackEventDelivery.objects.filter(status="waiting")
                if retained.filter(team_id=team_id).count() >= 200 or retained.count() >= 2000:
                    return Response({"error": "Too many pending setup events. Retry this event later."}, status=503)
            delivery, created = SlackEventDelivery.objects.get_or_create(
                id=event_id,
                defaults={
                    "connection": connection,
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "event": event.get("type"),
                    "body_hash": body_hash,
                    "payload": payload if active or waiting else {},
                    "status": "queued" if active else "waiting" if waiting else "ignored",
                },
            )
            if delivery.body_hash != body_hash or delivery.event != event.get("type"):
                return Response({"error": "Event identity was already used."}, status=409)
            if delivery.status in ("queued", "waiting", "awaiting_mapping", "retry") and active:
                transaction.on_commit(lambda: process_slack_event.delay(str(delivery.id)), robust=True)
        return Response({"status": delivery.status, "duplicate": not created}, status=202)


class CommandsEndpoint(BaseAPIView):
    """Slash-command entry point; answers help synchronously, queues the rest."""

    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [FormParser, MultiPartParser]

    def post(self, request):
        secret = configuration()["SIGNING_SECRET"]
        if not secret:
            return Response({"error": "Slack commands are not configured."}, status=503)
        try:
            content_length = int(request.META.get("CONTENT_LENGTH", "0") or 0)
        except ValueError:
            raise ValidationError("Invalid request length.")
        if content_length > 65536:
            return Response({"error": "Command is too large."}, status=413)
        raw = request.body
        if len(raw) > 65536:
            return Response({"error": "Command is too large."}, status=413)
        if not verify_signature(
            secret, request.headers.get("X-Slack-Request-Timestamp"), raw, request.headers.get("X-Slack-Signature")
        ):
            raise PermissionDenied("Invalid Slack signature.")
        if request.POST.get("command") != "/plane":
            raise ValidationError("Unknown command.")
        # Slack posts urlencoded bodies; the signed raw bytes back these values.
        team_id = services.slack_id(request.POST.get("team_id"))
        channel_id = services.slack_id(request.POST.get("channel_id"))
        user_id = services.slack_id(request.POST.get("user_id"))
        text = request.POST.get("text", "")
        if not isinstance(text, str) or len(text) > commands.MAX_TEXT:
            raise ValidationError("Invalid command text.")
        response_url = request.POST.get("response_url")
        if not (isinstance(response_url, str) and response_url.startswith(commands.RESPONSE_URL_PREFIX)):
            response_url = ""
        parsed = commands.parse(text)
        if parsed["action"] == "help":
            return Response({"response_type": "ephemeral", "text": commands.HELP_TEXT})
        run_slack_command.delay(
            {
                "command": "/plane",
                "team_id": team_id,
                "channel_id": channel_id,
                "user_id": user_id,
                "text": text,
                "response_url": response_url,
            }
        )
        return Response({"response_type": "ephemeral", "text": "Working — the result will appear here shortly."})
