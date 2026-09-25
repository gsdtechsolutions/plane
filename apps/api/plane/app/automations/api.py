# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from django.db import transaction
from rest_framework.permissions import BasePermission, SAFE_METHODS
from plane.db.models import ProjectMember, WorkspaceMember
from plane.app.automations.serializers import AutomationRuleSerializer
from plane.db.models import AutomationRule, Project
from plane.app.views.base import BaseAPIView, BaseViewSet


class AutomationRulePermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        workspace = WorkspaceMember.objects.filter(
            workspace__slug=view.workspace_slug, member=request.user, is_active=True
        )
        members = ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            project_id=view.project_id,
            member=request.user,
            is_active=True,
        )
        if not workspace.exists() or not members.exists():
            return False
        return request.method in SAFE_METHODS or members.filter(role=20).exists() or workspace.filter(role=20).exists()


class AutomationRuleViewSet(BaseViewSet):
    """CRUD for per-project board automation rules.

    Every query is bound to the workspace slug + project id from the URL, so a
    rule from another project/workspace is never reachable (404, not 403).
    """

    serializer_class = AutomationRuleSerializer
    model = AutomationRule
    permission_classes = [AutomationRulePermission]

    def get_queryset(self):
        return AutomationRule.objects.filter(
            workspace__slug=self.workspace_slug,
            project_id=self.project_id,
            deleted_at__isnull=True,
        ).order_by("-created_at")

    def get_object(self):
        queryset = self.get_queryset()
        rule_id = self.kwargs.get("rule_id")
        obj = get_object_or_404(queryset, id=rule_id)
        # May raise a permission denied (no-op for class-level permissions).
        self.check_object_permissions(self.request, obj)
        return obj

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["project"] = get_object_or_404(Project, id=self.project_id, workspace__slug=self.workspace_slug)
        return context

    def perform_create(self, serializer):
        project = Project.objects.get(id=self.project_id, workspace__slug=self.workspace_slug)
        # ProjectBaseModel.save() derives workspace from the project.
        serializer.save(project=project)

    def perform_destroy(self, instance):
        instance.delete()


class AutomationRuleToggleEndpoint(BaseAPIView):
    """POST: flip a rule's is_active flag. Project admins only (permission class)."""

    permission_classes = [AutomationRulePermission]

    @transaction.atomic
    def post(self, request, slug, project_id, rule_id):
        rule = get_object_or_404(
            AutomationRule.objects.select_for_update(),
            id=rule_id,
            project_id=project_id,
            workspace__slug=slug,
            deleted_at__isnull=True,
        )
        rule.is_active = not rule.is_active
        rule.save(update_fields=["is_active", "updated_at"])
        serializer = AutomationRuleSerializer(rule)
        return Response(serializer.data, status=status.HTTP_200_OK)
