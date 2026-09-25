# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Read-only proxy API for workspace infra connections and per-board links.

Connections (workspace admin only): CRUD + verify + resource discovery.
Links (project members read / project admin write): CRUD + combined live
status. Tokens stay server-side; every external call is SSRF-pinned.
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from plane.app.infra import coolify, grafana
from plane.app.infra.client import InfraConfigError, InfraUnavailable, cached_json
from plane.app.infra.permissions import InfraLinkPermission
from plane.app.infra.serializers import InfraConnectionSerializer, InfraLinkSerializer
from plane.app.permissions import allow_permission, ROLE
from plane.app.views.base import BaseAPIView, BaseViewSet
from plane.db.models import InfraConnection, Project, ProjectInfraLink, Workspace


def _connection_or_404(slug, connection_id):
    return get_object_or_404(
        InfraConnection, id=connection_id, workspace__slug=slug, deleted_at__isnull=True
    )


def _verify_connection(connection):
    try:
        if connection.service == "coolify":
            return coolify.verify(connection)
        if connection.service == "grafana":
            return grafana.verify(connection)
        return {"ok": False, "error": "Unknown service."}
    except (InfraUnavailable, InfraConfigError) as exc:
        return {"ok": False, "error": str(exc.detail)}


class InfraConnectionEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        connections = InfraConnection.objects.filter(workspace__slug=slug, deleted_at__isnull=True)
        serializer = InfraConnectionSerializer(connections, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        serializer = InfraConnectionSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            serializer.save(workspace_id=workspace.id)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class InfraConnectionDetailEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, connection_id):
        serializer = InfraConnectionSerializer(_connection_or_404(slug, connection_id))
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, connection_id):
        connection = _connection_or_404(slug, connection_id)
        serializer = InfraConnectionSerializer(connection, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, connection_id):
        connection = _connection_or_404(slug, connection_id)
        connection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InfraConnectionVerifyEndpoint(BaseAPIView):
    """POST: live auth probe against the configured service. Returns
    {"ok": true, "version": ...} or {"ok": false, "error": ...} — a failed
    probe is data, not a 5xx."""

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, connection_id):
        return Response(_verify_connection(_connection_or_404(slug, connection_id)), status=status.HTTP_200_OK)


class InfraConnectionResourcesEndpoint(BaseAPIView):
    """GET: discoverable resources on this connection for the pickers —
    ?resource=applications|dashboards (defaults by service) and ?refresh=1."""

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug, connection_id):
        connection = _connection_or_404(slug, connection_id)
        refresh = request.GET.get("refresh") == "1"
        cache_key = f"infra:conn:{connection.id}:resources"

        def produce():
            if connection.service == "coolify":
                return {"resource_type": "applications", "resources": coolify.list_applications(connection)}
            return {"resource_type": "dashboards", "resources": grafana.list_dashboards(connection)}

        try:
            payload = cached_json(cache_key, produce, refresh=refresh)
        except (InfraUnavailable, InfraConfigError) as exc:
            return Response({"error": str(exc.detail)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(payload, status=status.HTTP_200_OK)


class InfraLinkViewSet(BaseViewSet):
    """CRUD for per-board infra links. Every query is bound to the workspace
    slug + project id from the URL (404, never cross-project leakage)."""

    serializer_class = InfraLinkSerializer
    model = ProjectInfraLink
    permission_classes = [InfraLinkPermission]

    def get_queryset(self):
        return ProjectInfraLink.objects.filter(
            workspace__slug=self.workspace_slug,
            project_id=self.project_id,
            deleted_at__isnull=True,
        ).select_related("connection").order_by("-created_at")

    def get_object(self):
        queryset = self.get_queryset()
        link_id = self.kwargs.get("link_id")
        obj = get_object_or_404(queryset, id=link_id)
        self.check_object_permissions(self.request, obj)
        return obj

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["project"] = get_object_or_404(
            Project, id=self.project_id, workspace__slug=self.workspace_slug
        )
        return context

    def perform_create(self, serializer):
        project = Project.objects.get(id=self.project_id, workspace__slug=self.workspace_slug)
        # ProjectBaseModel.save() derives workspace from the project.
        serializer.save(project=project)

    def perform_destroy(self, instance):
        instance.delete()


class InfraStatusEndpoint(BaseAPIView):
    """GET: combined live status for every link on this board, in one call.
    A failing external service degrades that one entry to {"error": ...}
    instead of failing the tab."""

    permission_classes = [InfraLinkPermission]

    def get(self, request, slug, project_id):
        get_object_or_404(Project, id=project_id, workspace__slug=slug)
        refresh = request.GET.get("refresh") == "1"
        links = (
            ProjectInfraLink.objects.filter(
                project_id=project_id, workspace__slug=slug, deleted_at__isnull=True
            )
            .select_related("connection")
            .order_by("-created_at")
        )
        results = []
        for link in links:
            entry = {"link": InfraLinkSerializer(link).data, "status": None, "error": None}
            try:
                if link.kind == "coolify_app":
                    connection, app_uuid = link.connection, link.external_id

                    def produce(connection=connection, app_uuid=app_uuid):
                        return coolify.app_status(connection, app_uuid)

                    entry["status"] = cached_json(
                        f"infra:conn:{connection.id}:app:{app_uuid}", produce, refresh=refresh
                    )
                else:
                    entry["status"] = {
                        "kind": "grafana_dashboard",
                        "title": link.display_name or link.external_id,
                        "url": link.external_url,
                    }
            except (InfraUnavailable, InfraConfigError) as exc:
                entry["error"] = str(exc.detail)
            results.append(entry)
        return Response({"links": results}, status=status.HTTP_200_OK)
