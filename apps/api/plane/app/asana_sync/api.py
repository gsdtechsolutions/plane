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
from plane.app.asana_sync import mapping
from plane.app.asana_sync.client import AsanaAPIError, AsanaAuthError, AsanaClient
from plane.app.asana_sync.crypto import AsanaCryptoError, decrypt_token
from plane.app.asana_sync.engine import AsanaSyncEngine
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
    Label,
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
        if not workspace.get("gid"):
            return Response(
                {"verified": False, "error": "This token has no Asana workspaces or organizations."},
                status=status.HTTP_400_BAD_REQUEST,
            )
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


class AsanaWorkspaceSyncListEndpoint(_AsanaAccessMixin, BaseAPIView):
    """GET: every sync in the workspace with its project identity (workspace admins)."""

    def get(self, request, slug):
        if not self._require_workspace_admin():
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        syncs = (
            AsanaProjectSync.objects.filter(
                project__workspace__slug=slug, deleted_at__isnull=True
            )
            .select_related("project")
            .order_by("-created_at")
        )
        data = [
            {
                "id": str(sync.id),
                "project_id": str(sync.project_id),
                "project_name": sync.project.name,
                "connection": str(sync.connection_id),
                "asana_project_gid": sync.asana_project_gid,
                "asana_project_name": sync.asana_project_name,
                "direction": sync.direction,
                "sync_subtasks": sync.sync_subtasks,
                "sync_comments": sync.sync_comments,
                "webhook_configured": sync.webhook_configured,
                "initial_sync_done": sync.initial_sync_done,
                "last_synced_at": sync.last_synced_at,
                "is_active": sync.is_active,
            }
            for sync in syncs
        ]
        return Response(data)


def _require_sync_access(view, slug, sync_id) -> Response | None:
    """Workspace admin OR the sync's project admin; None means allowed."""
    sync = get_object_or_404(
        AsanaProjectSync, id=sync_id, project__workspace__slug=slug, deleted_at__isnull=True
    )
    is_workspace_admin = WorkspaceMember.objects.filter(
        workspace__slug=slug, member=view.request.user, role=20, is_active=True
    ).exists()
    is_project_admin = ProjectMember.objects.filter(
        project_id=sync.project_id, member=view.request.user, role=20, is_active=True
    ).exists()
    if not (is_workspace_admin or is_project_admin):
        return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
    return None


class AsanaWorkspaceSyncRunEndpoint(_AsanaAccessMixin, BaseAPIView):
    """POST: run one engine pass now. A redis lock keeps pile-ups away; the
    celery task clears it when the pass settles."""

    def post(self, request, slug, sync_id):
        denied = _require_sync_access(self, slug, sync_id)
        if denied:
            return denied
        ri = redis_instance()
        if not ri.set(f"asana_sync_lock:{sync_id}", "1", nx=True, ex=600):
            return Response({"detail": "A sync pass is already running."}, status=status.HTTP_409_CONFLICT)
        from plane.app.asana_sync.tasks import asana_sync_run

        asana_sync_run.delay(str(sync_id))
        return Response({"detail": "Sync queued."}, status=status.HTTP_202_ACCEPTED)


class AsanaWorkspaceSyncLogsEndpoint(_AsanaAccessMixin, BaseAPIView):
    """GET: recent engine log rows for a sync (newest first, capped)."""

    def get(self, request, slug, sync_id):
        denied = _require_sync_access(self, slug, sync_id)
        if denied:
            return denied
        sync = get_object_or_404(
            AsanaProjectSync, id=sync_id, project__workspace__slug=slug, deleted_at__isnull=True
        )
        logs = AsanaSyncLog.objects.filter(sync=sync, deleted_at__isnull=True)[:100]
        return Response(AsanaSyncLogSerializer(logs, many=True).data)


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


def _asana_member_names(sync: AsanaProjectSync, client: AsanaClient) -> dict[str, str]:
    """gid -> display name for members of the remote Asana project (best effort)."""
    names: dict[str, str] = {}
    try:
        for membership in client.project_memberships(sync.asana_project_gid)[:REMOTE_BROWSE_LIMIT]:
            member = membership.get("member") or {}
            if member.get("gid"):
                names[member["gid"]] = member.get("name") or ""
    except AsanaAPIError:
        logger.warning("Asana member browse failed for sync %s", sync.id, exc_info=True)
    return names


def _person_label_names(sync: AsanaProjectSync) -> dict[str, str]:
    """Person-label rows already provisioned by the engine, by label id."""
    label_ids = [
        mapping.assignee_value_label_id(raw)
        for raw in (sync.assignee_map or {}).values()
    ]
    label_ids = [lid for lid in label_ids if lid]
    if not label_ids:
        return {}
    return {str(l.id): l.name for l in Label.objects.filter(id__in=label_ids, project_id=sync.project_id)}


def _asana_assignee_payload(sync: AsanaProjectSync, client: AsanaClient) -> dict:
    asana_names = _asana_member_names(sync, client)
    label_names = _person_label_names(sync)
    assignee_map = sync.assignee_map or {}
    gids = list(dict.fromkeys(list(asana_names.keys()) + list(assignee_map.keys())))
    members = []
    for gid in gids:
        raw = assignee_map.get(gid)
        label_id = mapping.assignee_value_label_id(raw)
        members.append(
            {
                "gid": gid,
                "name": asana_names.get(gid) or (label_names.get(label_id, "") if label_id else "") or gid,
                "value": raw,
            }
        )
    plane_members = [
        {
            "id": str(pm.member_id),
            "name": (pm.member.display_name or pm.member.first_name or pm.member.username or "").strip(),
            "email": pm.member.email,
        }
        for pm in ProjectMember.objects.filter(project_id=sync.project_id, is_active=True)
        .select_related("member")
        .order_by("member__display_name")
    ]
    return {"members": members, "plane_members": plane_members, "map": assignee_map}


class _AsanaAssigneeMappingMixin:
    """Shared GET/PUT logic for the per-sync assignee mapping endpoints."""

    def _mapping_response(self, request, sync: AsanaProjectSync, slug: str) -> Response:
        client = self._client_for(sync.connection)
        if request.method == "GET":
            return Response(_asana_assignee_payload(sync, client))

        submitted = request.data.get("assignee_map")
        if not isinstance(submitted, dict):
            return Response(
                {"error": "assignee_map must be an object of gid -> member:<uuid> | label:<uuid> | label | auto | \"\""},
                status=status.HTTP_400_BAD_REQUEST,
            )
        active_member_ids = {
            str(mid)
            for mid in ProjectMember.objects.filter(project_id=sync.project_id, is_active=True).values_list(
                "member_id", flat=True
            )
        }
        project_label_ids = {
            str(lid) for lid in Label.objects.filter(project_id=sync.project_id).values_list("id", flat=True)
        }
        cleaned: dict[str, str] = {}
        for gid, value in submitted.items():
            gid = str(gid)
            value = "" if value is None else str(value)
            if not gid.isdigit() or len(gid) > 64:
                return Response({"error": f"Invalid Asana gid '{gid}'"}, status=status.HTTP_400_BAD_REQUEST)
            if value in ("", "auto", "label"):
                cleaned[gid] = value
            elif value.startswith(mapping.ASSIGNEE_MEMBER_PREFIX):
                if value[len(mapping.ASSIGNEE_MEMBER_PREFIX):] not in active_member_ids:
                    return Response(
                        {"error": f"{gid}: member is not an active project member"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                cleaned[gid] = value
            elif value.startswith(mapping.ASSIGNEE_LABEL_PREFIX):
                if value[len(mapping.ASSIGNEE_LABEL_PREFIX):] not in project_label_ids:
                    return Response(
                        {"error": f"{gid}: label does not belong to this project"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                cleaned[gid] = value
            else:
                return Response({"error": f"{gid}: unrecognized value '{value}'"}, status=status.HTTP_400_BAD_REQUEST)

        sync.assignee_map = cleaned
        sync.save(update_fields=["assignee_map", "updated_at"])

        # Provision person-label rows for the "label" shorthand via the engine
        # helper (reuses existing label rows, registers label:<id> values).
        if "label" in cleaned.values():
            engine = AsanaSyncEngine(sync, self._client_for(sync.connection))
            asana_names = _asana_member_names(sync, engine.client)
            for gid, value in cleaned.items():
                if value == "label":
                    engine._ensure_person_label(gid, asana_names.get(gid))
            sync.refresh_from_db(fields=["assignee_map"])
        return Response(_asana_assignee_payload(sync, self._client_for(sync.connection)))


class AsanaWorkspaceSyncAssigneesEndpoint(_AsanaAssigneeMappingMixin, _AsanaAccessMixin, BaseAPIView):
    """GET/PUT: the assignee mapping for one sync (workspace-admin or project-admin URL)."""

    def get(self, request, slug, sync_id):
        denied = _require_sync_access(self, slug, sync_id)
        if denied:
            return denied
        sync = get_object_or_404(
            AsanaProjectSync, id=sync_id, project__workspace__slug=slug, deleted_at__isnull=True
        )
        return self._mapping_response(request, sync, slug)

    def put(self, request, slug, sync_id):
        denied = _require_sync_access(self, slug, sync_id)
        if denied:
            return denied
        sync = get_object_or_404(
            AsanaProjectSync, id=sync_id, project__workspace__slug=slug, deleted_at__isnull=True
        )
        return self._mapping_response(request, sync, slug)


class AsanaSyncAssigneesEndpoint(_AsanaAssigneeMappingMixin, _AsanaAccessMixin, BaseAPIView):
    """GET/PUT: the assignee mapping for one sync (project-scoped URL)."""

    def get(self, request, slug, project_id, sync_id):
        if not self._require_project_admin(project_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        sync = get_object_or_404(
            AsanaProjectSync, id=sync_id, project_id=project_id, workspace__slug=slug, deleted_at__isnull=True
        )
        return self._mapping_response(request, sync, slug)

    def put(self, request, slug, project_id, sync_id):
        if not self._require_project_admin(project_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        sync = get_object_or_404(
            AsanaProjectSync, id=sync_id, project_id=project_id, workspace__slug=slug, deleted_at__isnull=True
        )
        return self._mapping_response(request, sync, slug)
