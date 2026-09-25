# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Asana sync API: workspace-level connections, per-project sync mappings,
remote browse (projects/sections/members/tags), webhook setup, run-now, logs."""

# Python imports
import logging

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Django imports
from django.shortcuts import get_object_or_404

# Module imports
from plane.app.asana_sync.client import AsanaAPIError, AsanaAuthError, AsanaClient
from plane.app.asana_sync.crypto import AsanaCryptoError, decrypt_token
from plane.app.asana_sync.serializers import (
    AsanaConnectionSerializer,
    AsanaProjectSyncSerializer,
    AsanaSyncLogSerializer,
)
from plane.app.views.base import BaseAPIView, BaseViewSet
from plane.db.models import (
    AsanaConnection,
    AsanaProjectSync,
    AsanaSyncLog,
    Project,
    ProjectMember,
    WorkspaceMember,
)
from plane.settings.redis import redis_instance

logger = logging.getLogger(__name__)

# Keep remote responses bounded so a huge Asana org can't blow up a request.
REMOTE_BROWSE_LIMIT = 200


class _AsanaAccessMixin:
    """Shared permission helpers: workspace admin for connections, project admin for syncs."""

    def _require_workspace_admin(self) -> bool:
        return (
            WorkspaceMember.objects.filter(
                workspace__slug=self.workspace_slug, member=self.request.user, role=20, is_active=True
            ).exists()
        )

    def _require_project_admin(self, project_id) -> bool:
        return (
            ProjectMember.objects.filter(
                workspace__slug=self.workspace_slug,
                project_id=project_id,
                member=self.request.user,
                role=20,
                is_active=True,
            ).exists()
        )

    def _client_for(self, connection: AsanaConnection) -> AsanaClient:
        return AsanaClient(decrypt_token(connection.pat_encrypted))


class AsanaConnectionViewSet(_AsanaAccessMixin, BaseViewSet):
    """CRUD for workspace-level Asana connections (workspace admins only)."""

    serializer_class = AsanaConnectionSerializer
    model = AsanaConnection

    def get_permissions(self):
        from rest_framework.permissions import BasePermission

        class _AdminOnly(BasePermission):
            def has_permission(self, request, view):
                if not request.user.is_authenticated:
                    return False
                return view._require_workspace_admin()

        return [_AdminOnly()]

    def get_queryset(self):
        return AsanaConnection.objects.filter(
            workspace__slug=self.workspace_slug, deleted_at__isnull=True
        ).order_by("-created_at")

    def perform_create(self, serializer):
        workspace = WorkspaceMember.objects.filter(
            workspace__slug=self.workspace_slug, member=self.request.user
        ).values_list("workspace_id", flat=True).first()
        serializer.save(workspace_id=workspace)


class AsanaConnectionVerifyEndpoint(_AsanaAccessMixin, BaseAPIView):
    """POST: validate the stored PAT against Asana and cache workspace identity."""

    def post(self, request, slug, connection_id):
        if not self._require_workspace_admin():
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        connection = get_object_or_404(
            AsanaConnection, id=connection_id, workspace__slug=slug, deleted_at__isnull=True
        )
        try:
            client = self._client_for(connection)
            me = client.me()
        except AsanaCryptoError as exc:
            return Response({"verified": False, "error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except AsanaAuthError:
            return Response(
                {"verified": False, "error": "Asana rejected the token. Reconnect with a fresh PAT."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except AsanaAPIError as exc:
            return Response({"verified": False, "error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        workspaces = client.workspaces()
        workspace = workspaces[0] if workspaces else {}
        from django.utils import timezone

        connection.asana_workspace_gid = workspace.get("gid", "")
        connection.asana_workspace_name = workspace.get("name", "")
        connection.last_verified_at = timezone.now()
        connection.save(update_fields=["asana_workspace_gid", "asana_workspace_name", "last_verified_at", "updated_at"])
        return Response(
            {
                "verified": True,
                "asana_user": {"gid": me.get("gid"), "name": me.get("name")},
                "asana_workspace": {
                    "gid": connection.asana_workspace_gid,
                    "name": connection.asana_workspace_name,
                },
            }
        )


class AsanaRemoteBrowseEndpoint(_AsanaAccessMixin, BaseAPIView):
    """GET: list remote projects (and optionally sections/members/tags) for a connection."""

    def get(self, request, slug, connection_id):
        if not self._require_workspace_admin():
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        connection = get_object_or_404(
            AsanaConnection, id=connection_id, workspace__slug=slug, deleted_at__isnull=True
        )
        try:
            client = self._client_for(connection)
            resource = request.query_params.get("resource", "projects")
            if resource == "projects":
                ws_gid = request.query_params.get("workspace_gid") or connection.asana_workspace_gid
                if not ws_gid:
                    return Response(
                        {"error": "workspace_gid required (verify the connection first)"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                projects = [
                    {"gid": p["gid"], "name": p.get("name", ""), "archived": p.get("archived", False)}
                    for p in client.projects(ws_gid)[:REMOTE_BROWSE_LIMIT]
                    if not p.get("archived", False)
                ]
                return Response({"projects": projects})
            project_gid = request.query_params.get("project_gid")
            if not project_gid:
                return Response({"error": "project_gid required"}, status=status.HTTP_400_BAD_REQUEST)
            if resource == "sections":
                return Response(
                    {"sections": [{"gid": s["gid"], "name": s.get("name", "")} for s in client.sections(project_gid)]}
                )
            if resource == "members":
                members = []
                for m in client.project_memberships(project_gid)[:REMOTE_BROWSE_LIMIT]:
                    member = m.get("member") or {}
                    if member.get("gid"):
                        members.append({"gid": member["gid"], "name": member.get("name", "")})
                return Response({"members": members})
            if resource == "tags":
                ws_gid = request.query_params.get("workspace_gid") or connection.asana_workspace_gid
                return Response(
                    {"tags": [{"gid": t["gid"], "name": t.get("name", "")} for t in client.tags(ws_gid)]}
                )
            return Response({"error": "unknown resource"}, status=status.HTTP_400_BAD_REQUEST)
        except AsanaCryptoError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except AsanaAuthError:
            return Response({"error": "Asana rejected the token."}, status=status.HTTP_400_BAD_REQUEST)
        except AsanaAPIError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


class AsanaProjectSyncViewSet(_AsanaAccessMixin, BaseViewSet):
    """CRUD for per-project Asana sync mappings (project admins only)."""

    serializer_class = AsanaProjectSyncSerializer
    model = AsanaProjectSync

    def get_permissions(self):
        from rest_framework.permissions import BasePermission

        class _ProjectAdminOnly(BasePermission):
            def has_permission(self, request, view):
                if not request.user.is_authenticated:
                    return False
                return view._require_project_admin(view.kwargs.get("project_id"))

        return [_ProjectAdminOnly()]

    def get_queryset(self):
        return AsanaProjectSync.objects.filter(
            project__workspace__slug=self.workspace_slug,
            project_id=self.project_id,
            deleted_at__isnull=True,
        ).order_by("-created_at")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["workspace_id"] = Project.objects.filter(
            id=self.project_id, workspace__slug=self.workspace_slug
        ).values_list("workspace_id", flat=True).first()
        context["project"] = Project.objects.filter(
            id=self.project_id, workspace__slug=self.workspace_slug
        ).first()
        return context

    def perform_create(self, serializer):
        project = Project.objects.get(id=self.project_id, workspace__slug=self.workspace_slug)
        sync = serializer.save(project=project)
        self._prime_section_map(sync)

    def _prime_section_map(self, sync: AsanaProjectSync):
        """Snapshot section names into state_map at map time so the UI can show them."""
        try:
            client = self._client_for(sync.connection)
            state_map = dict(sync.state_map or {})
            for section in client.sections(sync.asana_project_gid):
                entry = state_map.get(section["gid"]) or {}
                entry.setdefault("name", section.get("name", ""))
                state_map[section["gid"]] = entry
            if state_map != sync.state_map:
                sync.state_map = state_map
                sync.save(update_fields=["state_map", "updated_at"])
        except Exception:
            logger.warning("Asana section priming failed for sync %s", sync.id, exc_info=True)


class AsanaSyncWebhookEndpoint(_AsanaAccessMixin, BaseAPIView):
    """POST: create (or replace) the Asana webhook for a sync; DELETE removes it."""

    def post(self, request, slug, project_id, sync_id):
        if not self._require_project_admin(project_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        sync = get_object_or_404(
            AsanaProjectSync, id=sync_id, project_id=project_id, workspace__slug=slug, deleted_at__isnull=True
        )
        target = request.build_absolute_uri(f"/api/asana-sync/webhook/{sync.id}/")
        try:
            client = self._client_for(sync.connection)
            if sync.webhook_gid:
                try:
                    client.delete_webhook(sync.webhook_gid)
                except AsanaAPIError:
                    logger.info("Old Asana webhook %s already gone", sync.webhook_gid)
            webhook = client.create_webhook(sync.asana_project_gid, target)
        except AsanaCryptoError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except AsanaAuthError:
            return Response({"error": "Asana rejected the token."}, status=status.HTTP_400_BAD_REQUEST)
        except AsanaAPIError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        # The handshake POST carries the secret; webhook_configured flips there.
        sync.webhook_gid = webhook.get("gid", "")
        sync.save(update_fields=["webhook_gid", "updated_at"])
        return Response({"webhook_gid": sync.webhook_gid, "target": target, "awaiting_handshake": True})

    def delete(self, request, slug, project_id, sync_id):
        if not self._require_project_admin(project_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        sync = get_object_or_404(
            AsanaProjectSync, id=sync_id, project_id=project_id, workspace__slug=slug, deleted_at__isnull=True
        )
        if sync.webhook_gid:
            try:
                self._client_for(sync.connection).delete_webhook(sync.webhook_gid)
            except (AsanaAPIError, AsanaCryptoError):
                logger.info("Asana webhook %s removal failed (continuing)", sync.webhook_gid)
        sync.webhook_gid = ""
        sync.webhook_secret = ""
        sync.webhook_configured = False
        sync.save(update_fields=["webhook_gid", "webhook_secret", "webhook_configured", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class AsanaSyncRunEndpoint(_AsanaAccessMixin, BaseAPIView):
    """POST: run one engine pass now. A redis lock keeps pile-ups away; the
    celery task clears it when the pass settles."""

    def post(self, request, slug, project_id, sync_id):
        if not self._require_project_admin(project_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        get_object_or_404(
            AsanaProjectSync, id=sync_id, project_id=project_id, workspace__slug=slug, deleted_at__isnull=True
        )
        ri = redis_instance()
        if not ri.set(f"asana_sync_lock:{sync_id}", "1", nx=True, ex=600):
            return Response({"detail": "A sync pass is already running."}, status=status.HTTP_409_CONFLICT)
        from plane.app.asana_sync.tasks import asana_sync_run

        asana_sync_run.delay(str(sync_id))
        return Response({"detail": "Sync queued."}, status=status.HTTP_202_ACCEPTED)


class AsanaSyncLogsEndpoint(_AsanaAccessMixin, BaseAPIView):
    """GET: recent engine log rows for a sync (newest first, capped)."""

    def get(self, request, slug, project_id, sync_id):
        if not self._require_project_admin(project_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        sync = get_object_or_404(
            AsanaProjectSync, id=sync_id, project_id=project_id, workspace__slug=slug, deleted_at__isnull=True
        )
        logs = AsanaSyncLog.objects.filter(sync=sync, deleted_at__isnull=True)[:100]
        return Response(AsanaSyncLogSerializer(logs, many=True).data)
