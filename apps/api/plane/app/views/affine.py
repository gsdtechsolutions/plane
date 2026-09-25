# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""REST endpoints for the AFFiNE wiki integration.

Contract (all under /api/<version>/, workspace-scoped):
  POST   workspaces/<slug>/affine/probe/            -> list AFFiNE workspaces for a token
  GET    workspaces/<slug>/affine/connection/       -> current connection (or 404)
  POST   workspaces/<slug>/affine/connection/       -> create connection (validates live)
  PATCH  workspaces/<slug>/affine/connection/       -> update settings/token/project
  DELETE workspaces/<slug>/affine/connection/       -> disconnect (soft-deletes connection + maps)
  POST   workspaces/<slug>/affine/connection/sync/  -> run a sync pass now (sync or enqueue)
  GET    workspaces/<slug>/affine/connection/pages/ -> mapped pairs + statuses
"""
import logging

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import WorkSpaceAdminPermission
from plane.app.serializers.affine import (
    AffineConnectionCreateSerializer,
    AffineConnectionSerializer,
    AffineConnectionUpdateSerializer,
    AffinePageMapSerializer,
    AffineWorkspaceListRequestSerializer,
)
from plane.app.views.base import BaseAPIView
from plane.affine_sync.client import AffineClient, AffineError
from plane.affine_sync.engine import run_sync, verify_and_list_workspaces
from plane.db.models import AffineConnection, AffinePageMap, Workspace

logger = logging.getLogger(__name__)

SYNC_INLINE_PAGE_LIMIT = 25


class AffineProbeEndpoint(BaseAPIView):
    """Validate an AFFiNE token and list reachable workspaces (pre-connect)."""

    permission_classes = [WorkSpaceAdminPermission]

    def post(self, request, slug):
        serializer = AffineWorkspaceListRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            workspaces = verify_and_list_workspaces(
                serializer.validated_data["instance_url"],
                serializer.validated_data["api_token"],
            )
        except AffineError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"workspaces": workspaces}, status=status.HTTP_200_OK)


class AffineConnectionEndpoint(BaseAPIView):
    """CRUD for the single workspace connection."""

    permission_classes = [WorkSpaceAdminPermission]

    def _get_connection(self, slug) -> AffineConnection | None:
        return AffineConnection.objects.filter(workspace__slug=slug, deleted_at__isnull=True).first()

    def get(self, request, slug):
        connection = self._get_connection(slug)
        if connection is None:
            return Response({"error": "No AFFiNE connection configured."}, status=status.HTTP_404_NOT_FOUND)
        return Response(AffineConnectionSerializer(connection).data, status=status.HTTP_200_OK)

    def post(self, request, slug):
        if self._get_connection(slug) is not None:
            return Response(
                {"error": "An AFFiNE connection already exists for this workspace."},
                status=status.HTTP_409_CONFLICT,
            )
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response({"error": "Workspace not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = AffineConnectionCreateSerializer(
            data=request.data,
            context={"request": request, "workspace_slug": slug},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        connection = serializer.save(
            workspace=workspace,
            owned_by=request.user,
        )
        return Response(AffineConnectionSerializer(connection).data, status=status.HTTP_201_CREATED)

    def patch(self, request, slug):
        connection = self._get_connection(slug)
        if connection is None:
            return Response({"error": "No AFFiNE connection configured."}, status=status.HTTP_404_NOT_FOUND)
        serializer = AffineConnectionUpdateSerializer(
            connection,
            data=request.data,
            partial=True,
            context={"request": request, "workspace_slug": slug},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        updated = serializer.save()
        return Response(AffineConnectionSerializer(updated).data, status=status.HTTP_200_OK)

    def delete(self, request, slug):
        connection = self._get_connection(slug)
        if connection is None:
            return Response({"error": "No AFFiNE connection configured."}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            AffinePageMap.objects.filter(connection=connection).delete()
            connection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AffineSyncEndpoint(BaseAPIView):
    """Trigger a sync pass; inline for small mapping tables, celery otherwise."""

    permission_classes = [WorkSpaceAdminPermission]

    def post(self, request, slug):
        connection = AffineConnection.objects.filter(workspace__slug=slug, deleted_at__isnull=True).first()
        if connection is None:
            return Response({"error": "No AFFiNE connection configured."}, status=status.HTTP_404_NOT_FOUND)
        if not connection.is_active:
            return Response({"error": "AFFiNE sync is paused for this workspace."}, status=status.HTTP_400_BAD_REQUEST)

        mapped = AffinePageMap.objects.filter(connection=connection).count()
        if mapped > SYNC_INLINE_PAGE_LIMIT:
            from plane.bgtasks.affine_sync_task import affine_sync_connection

            affine_sync_connection.delay(str(connection.id))
            return Response({"queued": True, "page_count": mapped}, status=status.HTTP_202_ACCEPTED)

        try:
            stats = run_sync(connection)
        except AffineError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"queued": False, "stats": stats}, status=status.HTTP_200_OK)


class AffineSyncPagesEndpoint(BaseAPIView):
    """List mapped page pairs and their sync statuses."""

    permission_classes = [WorkSpaceAdminPermission]

    def get(self, request, slug):
        connection = AffineConnection.objects.filter(workspace__slug=slug, deleted_at__isnull=True).first()
        if connection is None:
            return Response({"error": "No AFFiNE connection configured."}, status=status.HTTP_404_NOT_FOUND)
        page_maps = (
            AffinePageMap.objects.filter(connection=connection)
            .select_related("page")
            .order_by("-last_synced_at")
        )
        return Response(AffinePageMapSerializer(page_maps, many=True).data, status=status.HTTP_200_OK)
