# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import hashlib
import hmac
import json
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
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.db.models import Workspace, WorkspaceMember, Project, ProjectMember, Issue
from plane.db.models.github_delivery import (
    GitHubConnection,
    GitHubConnectNonce,
    GitHubRepositoryMapping,
    GitHubPullRequest,
    GitHubIssueLink,
    GitHubWebhookDelivery,
)
from .client import GitHubClient, configuration, setup_state, require_configuration
from . import services
from .tasks import process_github_delivery, sync_github_mapping


def workspace_admin(user, slug):
    workspace = get_object_or_404(Workspace, slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=workspace, member=user, role=20, is_active=True).exists()
    ):
        raise PermissionDenied("Workspace administrators manage GitHub connections.")
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


def integer_input(value):
    return serializers.IntegerField(min_value=1).run_validation(value)


def redirect_to(url):
    response = HttpResponseRedirect(url)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def nonce_for_update(user, state, stage):
    if not isinstance(state, str) or not 32 <= len(state) <= 100:
        raise ValidationError("Connection request is invalid or expired. Start again.")
    nonce = get_object_or_404(
        GitHubConnectNonce.objects.select_for_update(),
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
        "installation_id": connection.installation_id,
        "account": connection.account_login,
        "active": connection.is_active,
    }


def mapping_data(mapping):
    return {
        "id": str(mapping.id),
        "connection_id": str(mapping.connection_id),
        "project_id": str(mapping.project_id),
        "repository_id": mapping.repository_id,
        "repository": mapping.full_name,
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
            connection_data(value) for value in GitHubConnection.objects.filter(workspace=workspace)
        ]
        state["mappings"] = [
            mapping_data(value)
            for value in GitHubRepositoryMapping.objects.filter(connection__workspace=workspace, is_active=True)
        ]
        return Response(state)


class ConnectEndpoint(BaseAPIView):
    def post(self, request, slug):
        workspace = workspace_admin(request.user, slug)
        client = GitHubClient()
        state = secrets.token_urlsafe(32)
        GitHubConnectNonce.objects.create(
            token_hash=hashlib.sha256(state.encode()).hexdigest(),
            workspace=workspace,
            user=request.user,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        return Response({"url": client.installation_url(state)})


class SetupEndpoint(BaseAPIView):
    def get(self, request):
        client = GitHubClient()
        installation_id = integer_input(request.query_params.get("installation_id"))
        with transaction.atomic():
            nonce = nonce_for_update(request.user, request.query_params.get("state"), "installation")
            state = secrets.token_urlsafe(32)
            nonce.token_hash = hashlib.sha256(state.encode()).hexdigest()
            nonce.installation_id = installation_id
            nonce.stage = "authorization"
            nonce.expires_at = timezone.now() + timedelta(minutes=10)
            nonce.save()
        return redirect_to(client.authorization_url(state))


class CallbackEndpoint(BaseAPIView):
    def get(self, request):
        client = GitHubClient()
        code = request.query_params.get("code")
        if not isinstance(code, str) or not 1 <= len(code) <= 1000:
            raise ValidationError("GitHub authorization did not complete. Start again.")
        with transaction.atomic():
            nonce = nonce_for_update(request.user, request.query_params.get("state"), "authorization")
            nonce.consumed_at = timezone.now()
            nonce.save(update_fields=["consumed_at"])
        installation, repositories, user = client.verify_installation(code, nonce.installation_id)
        allowed = [services.positive_id(repo.get("id")) for repo in repositories]
        github_user_id = services.positive_id(user.get("id"))
        account = services.text(installation.get("account", {}).get("login"), 255)
        if not account:
            raise ValidationError("GitHub installation account is missing.")
        with transaction.atomic():
            # Recheck membership after GitHub network requests.
            workspace_admin(request.user, nonce.workspace.slug)
            connection, _ = GitHubConnection.objects.select_for_update().get_or_create(
                installation_id=nonce.installation_id,
                defaults={
                    "workspace": nonce.workspace,
                    "github_user_id": github_user_id,
                    "account_login": account,
                    "connected_by": request.user,
                },
            )
            if connection.workspace_id != nonce.workspace_id:
                raise PermissionDenied("This installation is already connected to another workspace.")
            connection.account_login = account
            connection.github_user_id = github_user_id
            connection.authorized_repository_ids = allowed
            connection.connected_by = request.user
            connection.is_active = True
            connection.save()
            GitHubRepositoryMapping.objects.filter(connection=connection).exclude(repository_id__in=allowed).update(
                is_active=False
            )
        base = require_configuration()["BASE_URL"].rstrip("/")
        return redirect_to(f"{base}/{nonce.workspace.slug}/settings/integrations/?github=connected")


class DisconnectEndpoint(BaseAPIView):
    @transaction.atomic
    def delete(self, request, slug, connection_id):
        workspace = workspace_admin(request.user, slug)
        connection = get_object_or_404(
            GitHubConnection.objects.select_for_update(), id=connection_id, workspace=workspace
        )
        connection.is_active = False
        connection.save(update_fields=["is_active", "updated_at"])
        GitHubRepositoryMapping.objects.filter(connection=connection).update(is_active=False)
        return Response(status=204)


class RepositoriesEndpoint(BaseAPIView):
    def get(self, request, slug, connection_id):
        workspace = workspace_admin(request.user, slug)
        connection = get_object_or_404(GitHubConnection, id=connection_id, workspace=workspace, is_active=True)
        return Response(
            [
                {
                    "id": services.positive_id(repo.get("id")),
                    "full_name": services.repository_name(repo.get("full_name")),
                    "private": repo.get("private") is True,
                }
                for repo in GitHubClient().repositories(connection)
            ]
        )


class MappingsEndpoint(BaseAPIView):
    def post(self, request, slug):
        workspace = workspace_admin(request.user, slug)
        project = project_member(request.user, slug, uuid_input(request.data.get("project_id")))
        connection = get_object_or_404(
            GitHubConnection, id=uuid_input(request.data.get("connection_id")), workspace=workspace, is_active=True
        )
        repository_id = integer_input(request.data.get("repository_id"))
        repositories = GitHubClient().repositories(connection)
        repository = next((repo for repo in repositories if repo.get("id") == repository_id), None)
        if not repository:
            raise PermissionDenied("Select a repository authorized for this connection.")
        full_name = services.repository_name(repository.get("full_name"))
        with transaction.atomic():
            connection = GitHubConnection.objects.select_for_update().get(pk=connection.pk)
            if not connection.is_active or repository_id not in connection.authorized_repository_ids:
                raise PermissionDenied("This repository connection changed. Reload and try again.")
            workspace_admin(request.user, slug)
            project_member(request.user, slug, project.id)
            existing = GitHubRepositoryMapping.objects.filter(
                connection=connection, repository_id=repository_id, is_active=True
            ).first()
            if existing:
                if existing.project_id != project.id:
                    raise ValidationError(
                        "This repository is mapped to another project. Disconnect that mapping first."
                    )
                return Response(mapping_data(existing))
            mapping = GitHubRepositoryMapping.objects.create(
                connection=connection,
                project=project,
                repository_id=repository_id,
                full_name=full_name,
                is_private=repository.get("private") is True,
            )
            transaction.on_commit(lambda: sync_github_mapping.delay(str(mapping.id)), robust=True)
        return Response(mapping_data(mapping), status=201)


class MappingDetailEndpoint(BaseAPIView):
    @transaction.atomic
    def delete(self, request, slug, mapping_id):
        workspace = workspace_admin(request.user, slug)
        mapping = get_object_or_404(
            GitHubRepositoryMapping.objects.select_for_update(), id=mapping_id, connection__workspace=workspace
        )
        mapping.is_active = False
        mapping.save(update_fields=["is_active"])
        return Response(status=204)

    def post(self, request, slug, mapping_id):
        workspace = workspace_admin(request.user, slug)
        mapping = get_object_or_404(
            GitHubRepositoryMapping,
            id=mapping_id,
            connection__workspace=workspace,
            is_active=True,
            connection__is_active=True,
        )
        mapping.sync_status = "pending"
        mapping.sync_error = ""
        mapping.save(update_fields=["sync_status", "sync_error"])
        sync_github_mapping.delay(str(mapping.id))
        return Response(mapping_data(mapping), status=202)


class ProjectDevelopmentEndpoint(BaseAPIView):
    def get(self, request, slug, project_id):
        project = project_member(request.user, slug, project_id)
        mappings = GitHubRepositoryMapping.objects.filter(
            project=project, connection__workspace_id=project.workspace_id
        ).select_related("connection")
        pulls = (
            services.project_pull_requests(project).select_related("mapping__connection").order_by("-updated_at")[:200]
        )
        return Response(
            {
                "repositories": [mapping_data(value) for value in mappings],
                "pull_requests": [services.pull_request_data(value) for value in pulls],
                "releases": services.list_project_releases(project),
            }
        )


class IssuePullRequestsEndpoint(BaseAPIView):
    def issue(self, request, slug, project_id, issue_id):
        project = project_member(request.user, slug, project_id)
        return get_object_or_404(Issue, id=issue_id, project=project, workspace_id=project.workspace_id)

    def get(self, request, slug, project_id, issue_id):
        return Response(services.list_issue_pull_requests(self.issue(request, slug, project_id, issue_id)))

    def post(self, request, slug, project_id, issue_id):
        issue = self.issue(request, slug, project_id, issue_id)
        if request.data.get("pull_request_id"):
            pr = get_object_or_404(
                GitHubPullRequest,
                id=uuid_input(request.data["pull_request_id"]),
                mapping__project_id=issue.project_id,
                mapping__connection__workspace_id=issue.workspace_id,
            )
        else:
            url = request.data.get("url")
            match = re.fullmatch(
                r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]{0,8})/?",
                url if isinstance(url, str) else "",
            )
            if not match:
                raise ValidationError("Enter a GitHub pull request URL from a mapped repository.")
            mapping = get_object_or_404(
                GitHubRepositoryMapping.objects.select_related("connection", "project"),
                project_id=issue.project_id,
                connection__workspace_id=issue.workspace_id,
                full_name__iexact=match[1],
                is_active=True,
                connection__is_active=True,
            )
            data = GitHubClient().pull_request(mapping, int(match[2]))
            if not isinstance(data, dict) or data.get("number") != int(match[2]):
                raise ValidationError("GitHub returned a different pull request.")
            with transaction.atomic():
                connection = GitHubConnection.objects.select_for_update().get(id=mapping.connection_id)
                mapping = (
                    GitHubRepositoryMapping.objects.select_for_update()
                    .select_related("connection", "project")
                    .get(id=mapping.id)
                )
                if not connection.is_active or not mapping.is_active:
                    raise PermissionDenied("This repository is disconnected.")
                pr = services.upsert_pull_request(mapping, data)
        GitHubIssueLink.objects.update_or_create(
            issue=issue, pull_request=pr, defaults={"is_manual": True, "is_suppressed": False}
        )
        return Response(services.list_issue_pull_requests(issue), status=201)

    def delete(self, request, slug, project_id, issue_id, pull_request_id):
        issue = self.issue(request, slug, project_id, issue_id)
        pr = get_object_or_404(
            GitHubPullRequest,
            id=pull_request_id,
            mapping__project_id=issue.project_id,
            mapping__connection__workspace_id=issue.workspace_id,
        )
        GitHubIssueLink.objects.update_or_create(
            issue=issue, pull_request=pr, defaults={"is_manual": False, "is_suppressed": True}
        )
        return Response(status=204)


class WebhookEndpoint(BaseAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        secret = configuration()["WEBHOOK_SECRET"]
        if not secret:
            return Response({"error": "GitHub webhooks are not configured."}, status=503)
        try:
            content_length = int(request.META.get("CONTENT_LENGTH", "0") or 0)
        except ValueError:
            raise ValidationError("Invalid request length.")
        if content_length > 1048576:
            return Response({"error": "Webhook is too large."}, status=413)
        raw = request.body
        if len(raw) > 1048576:
            return Response({"error": "Webhook is too large."}, status=413)
        expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.encode(), request.headers.get("X-Hub-Signature-256", "").encode()):
            raise PermissionDenied("Invalid GitHub signature.")
        try:
            delivery_id = UUID(request.headers.get("X-GitHub-Delivery", ""))
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValidationError("Invalid GitHub delivery.")
        event = request.headers.get("X-GitHub-Event", "")
        if event not in {"pull_request", "pull_request_review", "release", "installation", "installation_repositories"}:
            return Response({"status": "ignored"}, status=202)
        installation = payload.get("installation")
        if not isinstance(installation, dict):
            raise ValidationError("Invalid GitHub installation.")
        installation_id = services.positive_id(installation.get("id"))
        body_hash = hashlib.sha256(raw).hexdigest()
        # Installation authorization rechecks current lifecycle/repository access.
        # There is no useful pre-connect projection for created/added events.
        if event == "installation" and payload.get("action") not in ("deleted", "suspend"):
            return Response({"status": "ignored"}, status=202)
        if event == "installation_repositories" and not payload.get("repositories_removed"):
            return Response({"status": "ignored"}, status=202)
        if event in services.CONTENT_EVENTS and not isinstance(payload.get("repository"), dict):
            raise ValidationError("Invalid GitHub repository.")
        if event in services.CONTENT_EVENTS:
            services.positive_id(payload["repository"].get("id"))
        with transaction.atomic():
            connection = GitHubConnection.objects.filter(installation_id=installation_id).first()
            waiting = connection is None and event in services.CONTENT_EVENTS
            active = connection is not None and connection.is_active
            existing = GitHubWebhookDelivery.objects.filter(id=delivery_id).first()
            if waiting and not existing:
                retained = GitHubWebhookDelivery.objects.filter(status="waiting")
                if retained.filter(installation_id=installation_id).count() >= 200 or retained.count() >= 2000:
                    return Response({"error": "Too many pending setup events. Retry this delivery later."}, status=503)
            delivery, created = GitHubWebhookDelivery.objects.get_or_create(
                id=delivery_id,
                defaults={
                    "connection": connection,
                    "installation_id": installation_id,
                    "repository_id": payload.get("repository", {}).get("id")
                    if event in services.CONTENT_EVENTS
                    else None,
                    "event": event,
                    "body_hash": body_hash,
                    "payload": payload if active or waiting else {},
                    "status": "queued" if active else "waiting" if waiting else "ignored",
                },
            )
            if delivery.body_hash != body_hash or delivery.event != event:
                return Response({"error": "Delivery identity was already used."}, status=409)
            if delivery.status in ("queued", "waiting", "awaiting_mapping", "retry") and active:
                transaction.on_commit(lambda: process_github_delivery.delay(str(delivery.id)), robust=True)
        return Response({"status": delivery.status, "duplicate": not created}, status=202)
