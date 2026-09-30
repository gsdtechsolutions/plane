# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.db.models import Project, Workspace, WorkspaceMember
from plane.db.models.ai_audit import AIActionAudit


def workspace_admin(user, slug):
    workspace = get_object_or_404(Workspace, slug=slug)
    if (
        not user.is_authenticated
        or not WorkspaceMember.objects.filter(workspace=workspace, member=user, role=20, is_active=True).exists()
    ):
        raise PermissionDenied("Workspace administrators manage the AI audit trail.")
    return workspace


class AIActionAuditSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()
    project_name = serializers.CharField(source="project.name", read_only=True, default=None)

    class Meta:
        model = AIActionAudit
        fields = (
            "id",
            "action",
            "entity_type",
            "entity_id",
            "model",
            "status",
            "input_excerpt",
            "output_excerpt",
            "error",
            "latency_ms",
            "metadata",
            "created_at",
            "actor",
            "actor_display",
            "project",
            "project_name",
        )

    def get_actor_display(self, obj):
        if obj.actor_id is None:
            return None
        return getattr(obj.actor, "display_name", None) or obj.actor.email


class AIActionAuditListEndpoint(BaseAPIView):
    """Read-only audit trail for AI actions. Workspace admins only."""

    def get(self, request, slug):
        workspace = workspace_admin(request.user, slug)
        queryset = AIActionAudit.objects.filter(workspace=workspace).select_related("actor", "project")

        params = request.query_params
        if params.get("action"):
            queryset = queryset.filter(action=params["action"])
        if params.get("entity_type"):
            queryset = queryset.filter(entity_type=params["entity_type"])
        if params.get("entity_id"):
            queryset = queryset.filter(entity_id=params["entity_id"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("project"):
            queryset = queryset.filter(project__id=params["project"])
        if params.get("search"):
            needle = params["search"].strip()
            if needle:
                queryset = queryset.filter(
                    Q(action__icontains=needle)
                    | Q(input_excerpt__icontains=needle)
                    | Q(output_excerpt__icontains=needle)
                    | Q(error__icontains=needle)
                )

        try:
            offset = max(int(params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = min(max(int(params.get("limit", 50)), 1), 200)
        except (TypeError, ValueError):
            limit = 50

        total = queryset.count()
        rows = queryset[offset : offset + limit]
        return Response(
            {
                "count": total,
                "offset": offset,
                "limit": limit,
                "results": AIActionAuditSerializer(rows, many=True).data,
            },
            status=status.HTTP_200_OK,
        )
