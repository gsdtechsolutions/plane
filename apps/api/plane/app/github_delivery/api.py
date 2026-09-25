# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import hashlib
import hmac
import html
import json
import re
import secrets
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.db.models import Workspace, WorkspaceMember, Project, ProjectMember, Issue
from plane.db.models.github_delivery import (
    GitHubApp,
    GitHubConnection,
    GitHubConnectNonce,
    GitHubRepositoryMapping,
    GitHubPullRequest,
    GitHubIssueLink,
    GitHubWebhookDelivery,
)
from .client import (
    GITHUB_COM,
    GitHubClient,
    board_origin,
    host_display,
    normalize_host,
    normalize_organization,
    web_base,
)
from .crypto import decrypt_secret, encrypt_secret
from . import services
from .tasks import process_github_delivery, search_github_issue_mentions, sync_github_mapping


def workspace_admin(user, slug):
    workspace = get_object_or_404(Workspace, slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=workspace, member=user, role=20, is_active=True).exists()
    ):
        raise PermissionDenied("Workspace administrators manage GitHub connections.")
    return workspace


def connector(user, slug):
    """Workspace admins and project admins may start or complete a connection."""
    workspace = get_object_or_404(Workspace, slug=slug)
    if not user.is_authenticated or not WorkspaceMember.objects.filter(
        workspace=workspace, member=user, is_active=True
    ).exists():
        raise PermissionDenied()
    if not WorkspaceMember.objects.filter(workspace=workspace, member=user, role=20, is_active=True).exists():
        if not ProjectMember.objects.filter(
            project__workspace=workspace, member=user, role=20, is_active=True
        ).exists():
            raise PermissionDenied("An active project administrator role is required.")
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


def project_connector(user, slug, project_id):
    """Workspace admins, or admins of this specific project, may map repositories."""
    if WorkspaceMember.objects.filter(workspace__slug=slug, member=user, role=20, is_active=True).exists():
        return project_member(user, slug, project_id)
    return project_member(user, slug, project_id, admin=True)


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
    connector(user, nonce.workspace.slug)
    return nonce


def rotate_nonce(nonce, stage, *, app=None, installation_id=None, expires_minutes=10):
    state = secrets.token_urlsafe(32)
    nonce.token_hash = hashlib.sha256(state.encode()).hexdigest()
    nonce.stage = stage
    nonce.consumed_at = None
    if app is not None:
        nonce.app = app
    if installation_id is not None:
        nonce.installation_id = installation_id
    nonce.expires_at = timezone.now() + timedelta(minutes=expires_minutes)
    nonce.save()
    return state


def connection_client(connection):
    if connection.app_id is None:
        raise ValidationError("This GitHub connection predates click-to-connect. Reconnect the account.")
    return GitHubClient(connection.host, connection.app)


def connection_data(connection):
    return {
        "id": str(connection.id),
        "installation_id": connection.installation_id,
        "account": connection.account_login,
        "host": connection.host,
        "host_display": host_display(connection.host),
        "app_slug": connection.app.slug if connection.app_id else None,
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
        origin = board_origin(request) or ""
        state = {
            "connect_urls": {
                "setup_url": f"{origin}/api/github-delivery/setup/" if origin else None,
                "callback_url": f"{origin}/api/github-delivery/callback/" if origin else None,
                "webhook_url": f"{origin}/api/github-delivery/webhooks/" if origin else None,
            },
            "permissions": ["Metadata: read", "Pull requests: read", "Contents: read"],
            "connections": [connection_data(value) for value in GitHubConnection.objects.filter(workspace=workspace)],
            "mappings": [
                mapping_data(value)
                for value in GitHubRepositoryMapping.objects.filter(connection__workspace=workspace, is_active=True)
            ],
        }
        return Response(state)


class ConnectEndpoint(BaseAPIView):
    def post(self, request, slug):
        workspace = connector(request.user, slug)
        account_type = request.data.get("account_type", "personal")
        if account_type not in ("personal", "organization", "enterprise"):
            raise ValidationError("Choose a personal account, an organization, or a GitHub Enterprise server.")
        organization = ""
        if account_type == "organization" or (
            account_type == "enterprise" and request.data.get("organization", "")
        ):
            organization = normalize_organization(request.data.get("organization")) or ""
            if not organization:
                raise ValidationError(
                    "Enter the GitHub organization login, for example my-company. "
                    "You must be allowed to create GitHub Apps for it."
                )
        host = GITHUB_COM if account_type != "enterprise" else normalize_host(request.data.get("enterprise_url"))
        if not host:
            raise ValidationError("Enter your GitHub Enterprise address, for example https://github.example.com")
        origin = board_origin(request)
        if not origin:
            raise ValidationError(
                "The public address of this board could not be determined. "
                "An instance administrator can set GITHUB_APP_BASE_URL."
            )
        state = secrets.token_urlsafe(32)
        GitHubConnectNonce.objects.create(
            token_hash=hashlib.sha256(state.encode()).hexdigest(),
            workspace=workspace,
            user=request.user,
            stage="manifest",
            host=host,
            organization=organization,
            origin=origin,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        return Response({"url": f"{origin}/api/github-delivery/manifest/start/?state={state}"})


class ManifestStartEndpoint(BaseAPIView):
    """Render the auto-submitting form that registers a GitHub App from a manifest."""

    def get(self, request):
        state = request.query_params.get("state")
        if not isinstance(state, str) or not 32 <= len(state) <= 100:
            raise ValidationError("Connection request is invalid or expired. Start again.")
        nonce = get_object_or_404(
            GitHubConnectNonce,
            token_hash=hashlib.sha256(state.encode()).hexdigest(),
            user=request.user,
            stage="manifest",
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        connector(request.user, nonce.workspace.slug)
        origin = nonce.origin
        manifest = {
            # GitHub App names are globally unique: suffix the organization so
            # an org-owned App for this workspace doesn't collide with the
            # personal-account App (GitHub's form stays editable either way).
            "name": f"Plane {nonce.workspace.name} {nonce.organization or 'delivery'}"[:34],
            "url": origin,
            "redirect_url": f"{origin}/api/github-delivery/manifest/callback/",
            "callback_urls": [f"{origin}/api/github-delivery/callback/"],
            "setup_url": f"{origin}/api/github-delivery/setup/",
            "request_oauth_on_install": False,
            "hook_attributes": {"url": f"{origin}/api/github-delivery/webhooks/", "active": True},
            "public": False,
            "default_permissions": {"metadata": "read", "pull_requests": "read", "contents": "read"},
            "default_events": ["pull_request", "pull_request_review", "release", "push"],
        }
        # An organization login registers the App under that organization
        # (github.com/organizations/<org>/settings/apps/new — same manifest
        # flow, the App is owned by the organization instead of the user).
        action = (
            f"{web_base(nonce.host)}/organizations/{nonce.organization}/settings/apps/new"
            if nonce.organization
            else f"{web_base(nonce.host)}/settings/apps/new"
        )
        body = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            "<title>Connect GitHub</title></head><body>"
            "<p>Connecting GitHub…</p>"
            f'<form id="connect" method="post" action="{html.escape(action, quote=True)}">'
            f'<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest), quote=True)}">'
            f'<input type="hidden" name="state" value="{html.escape(state, quote=True)}">'
            '<noscript><button type="submit">Continue to GitHub</button></noscript>'
            '</form><script>document.getElementById("connect").submit()</script></body></html>'
        )
        response = HttpResponse(body, content_type="text/html; charset=utf-8")
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class ManifestCallbackEndpoint(BaseAPIView):
    def get(self, request):
        code = request.query_params.get("code")
        if not isinstance(code, str) or not 1 <= len(code) <= 255:
            raise ValidationError("GitHub App creation did not complete. Start again.")
        with transaction.atomic():
            nonce = nonce_for_update(request.user, request.query_params.get("state"), "manifest")
            nonce.consumed_at = timezone.now()
            nonce.save(update_fields=["consumed_at"])
        credentials = GitHubClient(nonce.host).exchange_manifest_code(code)
        with transaction.atomic():
            connector(request.user, nonce.workspace.slug)
            app, _ = GitHubApp.objects.select_for_update().get_or_create(
                host=nonce.host,
                app_id=credentials["app_id"],
                defaults={
                    "slug": credentials["slug"],
                    "client_id": credentials["client_id"],
                    "client_secret": encrypt_secret(credentials["client_secret"]),
                    "private_key": encrypt_secret(credentials["private_key"]),
                    "webhook_secret": encrypt_secret(credentials["webhook_secret"]),
                    "created_by": request.user,
                },
            )
            state = rotate_nonce(nonce, "installation", app=app, expires_minutes=30)
            url = GitHubClient(nonce.host, app).installation_url(state)
        return redirect_to(url)


class SetupEndpoint(BaseAPIView):
    def get(self, request):
        installation_id = integer_input(request.query_params.get("installation_id"))
        with transaction.atomic():
            nonce = nonce_for_update(request.user, request.query_params.get("state"), "installation")
            if nonce.app_id is None:
                raise ValidationError("Connection request is invalid or expired. Start again.")
            state = rotate_nonce(nonce, "authorization", installation_id=installation_id)
        redirect_uri = f"{nonce.origin}/api/github-delivery/callback/"
        return redirect_to(GitHubClient(nonce.host, nonce.app).authorization_url(state, redirect_uri))


class CallbackEndpoint(BaseAPIView):
    def get(self, request):
        code = request.query_params.get("code")
        if not isinstance(code, str) or not 1 <= len(code) <= 1000:
            raise ValidationError("GitHub authorization did not complete. Start again.")
        with transaction.atomic():
            nonce = nonce_for_update(request.user, request.query_params.get("state"), "authorization")
            nonce.consumed_at = timezone.now()
            nonce.save(update_fields=["consumed_at"])
        if nonce.app_id is None or nonce.installation_id is None:
            raise ValidationError("Connection request is invalid or expired. Start again.")
        redirect_uri = f"{nonce.origin}/api/github-delivery/callback/"
        installation, repositories, user = GitHubClient(nonce.host, nonce.app).verify_installation(
            code, nonce.installation_id, redirect_uri
        )
        allowed = [services.positive_id(repo.get("id")) for repo in repositories]
        github_user_id = services.positive_id(user.get("id"))
        account = services.text(installation.get("account", {}).get("login"), 255)
        if not account:
            raise ValidationError("GitHub installation account is missing.")
        with transaction.atomic():
            # Recheck membership after GitHub network requests.
            connector(request.user, nonce.workspace.slug)
            connection, _ = GitHubConnection.objects.select_for_update().get_or_create(
                host=nonce.host,
                installation_id=nonce.installation_id,
                defaults={
                    "workspace": nonce.workspace,
                    "app": nonce.app,
                    "github_user_id": github_user_id,
                    "account_login": account,
                    "connected_by": request.user,
                },
            )
            if connection.workspace_id != nonce.workspace_id:
                raise PermissionDenied("This installation is already connected to another workspace.")
            if connection.app_id != nonce.app_id:
                raise PermissionDenied("This installation belongs to a different GitHub App. Start again.")
            connection.app = nonce.app
            connection.account_login = account
            connection.github_user_id = github_user_id
            connection.authorized_repository_ids = allowed
            connection.connected_by = request.user
            connection.is_active = True
            connection.save()
            GitHubRepositoryMapping.objects.filter(connection=connection).exclude(repository_id__in=allowed).update(
                is_active=False
            )
        return redirect_to(f"{nonce.origin}/{nonce.workspace.slug}/settings/integrations/?github=connected")


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
        workspace = connector(request.user, slug)
        connection = get_object_or_404(GitHubConnection, id=connection_id, workspace=workspace, is_active=True)
        return Response(
            [
                {
                    "id": services.positive_id(repo.get("id")),
                    "full_name": services.repository_name(repo.get("full_name")),
                    "private": repo.get("private") is True,
                }
                for repo in connection_client(connection).repositories(connection)
            ]
        )


class MappingsEndpoint(BaseAPIView):
    def post(self, request, slug):
        workspace = get_object_or_404(Workspace, slug=slug)
        project = project_connector(request.user, slug, uuid_input(request.data.get("project_id")))
        connection = get_object_or_404(
            GitHubConnection, id=uuid_input(request.data.get("connection_id")), workspace=workspace, is_active=True
        )
        repository_id = integer_input(request.data.get("repository_id"))
        repositories = connection_client(connection).repositories(connection)
        repository = next((repo for repo in repositories if repo.get("id") == repository_id), None)
        if not repository:
            raise PermissionDenied("Select a repository authorized for this connection.")
        full_name = services.repository_name(repository.get("full_name"))
        with transaction.atomic():
            connection = GitHubConnection.objects.select_for_update().get(pk=connection.pk)
            if not connection.is_active or repository_id not in connection.authorized_repository_ids:
                raise PermissionDenied("This repository connection changed. Reload and try again.")
            project_connector(request.user, slug, project.id)
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


PULL_URL_PATTERN = r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]{0,8})/?"


class IssuePullRequestsEndpoint(BaseAPIView):
    def issue(self, request, slug, project_id, issue_id):
        project = project_member(request.user, slug, project_id)
        return get_object_or_404(Issue, id=issue_id, project=project, workspace_id=project.workspace_id)

    def get(self, request, slug, project_id, issue_id):
        issue = self.issue(request, slug, project_id, issue_id)
        if services.queue_mention_search(issue):
            # The search backfill finds historical mentions beyond the sync
            # window; the client polls while it runs and revalidates after.
            transaction.on_commit(
                lambda: search_github_issue_mentions.delay(str(issue.id)), robust=True
            )
        return Response(services.list_issue_development(issue))

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
            mapping, number = self.resolve_pull_url(issue, request.data.get("url"))
            if mapping is None:
                # A well-formed URL for a host this project uses but no longer
                # maps reads as gone; anything else is a bad request.
                if self.matched_host_url:
                    raise NotFound("No connected repository matches this pull request URL.")
                raise ValidationError("Enter a GitHub pull request URL from a connected repository.")
            data = connection_client(mapping.connection).pull_request(mapping, number)
            if not isinstance(data, dict) or data.get("number") != number:
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
        return Response(services.list_issue_development(issue), status=201)

    def resolve_pull_url(self, issue, url):
        """Match a manual pull-request URL against every host this project uses.

        Sets matched_host_url when the URL was well-formed for a known host, so
        callers can answer 404 (unmapped) instead of 400 (malformed).
        """
        self.matched_host_url = False
        if not isinstance(url, str):
            return None, None
        hosts = set(
            GitHubRepositoryMapping.objects.filter(
                project_id=issue.project_id, is_active=True, connection__is_active=True
            ).values_list("connection__host", flat=True)
        ) or {GITHUB_COM}
        for host in hosts:
            match = re.fullmatch(re.escape(web_base(host)) + "/" + PULL_URL_PATTERN, url)
            if not match:
                continue
            self.matched_host_url = True
            mapping = (
                GitHubRepositoryMapping.objects.select_related("connection", "project")
                .filter(
                    project_id=issue.project_id,
                    connection__workspace_id=issue.workspace_id,
                    full_name__iexact=match[1],
                    is_active=True,
                    connection__is_active=True,
                )
                .first()
            )
            if mapping:
                return mapping, int(match[2])
        return None, None

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
        try:
            content_length = int(request.META.get("CONTENT_LENGTH", "0") or 0)
        except ValueError:
            raise ValidationError("Invalid request length.")
        if content_length > 1048576:
            return Response({"error": "Webhook is too large."}, status=413)
        raw = request.body
        if len(raw) > 1048576:
            return Response({"error": "Webhook is too large."}, status=413)
        app = self.authenticated_app(raw, request.headers.get("X-Hub-Signature-256", ""))
        if app is None:
            raise PermissionDenied("Invalid GitHub signature.")
        try:
            delivery_id = UUID(request.headers.get("X-GitHub-Delivery", ""))
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValidationError("Invalid GitHub delivery.")
        event = request.headers.get("X-GitHub-Event", "")
        if event not in {
            "pull_request",
            "pull_request_review",
            "release",
            "push",
            "installation",
            "installation_repositories",
        }:
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
            # The delivery is authenticated as this App; only installations of
            # the same App may project. Mixed-App deliveries stay inert.
            connection = GitHubConnection.objects.filter(app=app, installation_id=installation_id).first()
            waiting = connection is None and event in services.CONTENT_EVENTS
            active = connection is not None and connection.is_active
            existing = GitHubWebhookDelivery.objects.filter(id=delivery_id).first()
            if waiting and not existing:
                retained = GitHubWebhookDelivery.objects.filter(status="waiting")
                if (
                    retained.filter(host=app.host, installation_id=installation_id).count() >= 200
                    or retained.count() >= 2000
                ):
                    return Response({"error": "Too many pending setup events. Retry this delivery later."}, status=503)
            delivery, created = GitHubWebhookDelivery.objects.get_or_create(
                id=delivery_id,
                defaults={
                    "connection": connection,
                    "app": app,
                    "host": app.host,
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

    def authenticated_app(self, raw, signature):
        """Identify the GitHub App whose webhook secret signed this delivery."""
        if not signature.startswith("sha256="):
            return None
        for candidate in GitHubApp.objects.all():
            try:
                expected = "sha256=" + hmac.new(
                    decrypt_secret(candidate.webhook_secret).encode(), raw, hashlib.sha256
                ).hexdigest()
            except Exception:
                continue
            if hmac.compare_digest(expected, signature):
                return candidate
        return None
