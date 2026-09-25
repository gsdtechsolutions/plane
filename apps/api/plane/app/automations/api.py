# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ProjectBasePermission
from plane.app.automations.serializers import AutomationRuleSerializer
from plane.db.models import AutomationRule, Project
from .base import BaseAPIView, BaseViewSet


class AutomationRuleViewSet(BaseViewSet):
    """CRUD for per-project board automation rules.

    Every query is bound to the workspace slug + project id from the URL, so a
    rule from another project/workspace is never reachable (404, not 403).
    """

    serializer_class = AutomationRuleSerializer
    model = AutomationRule
    permission_classes = [ProjectBasePermission]

    def get_queryset(self):
        return (
            AutomationRule.objects.filter(
                workspace__slug=self.workspace_slug,
                project_id=self.project_id,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
        )

    def get_object(self):
        queryset = self.get_queryset()
        rule_id = self.kwargs.get("rule_id")
        obj = get_object_or_404(queryset, id=rule_id)
        # May raise a permission denied (no-op for class-level permissions).
        self.check_object_permissions(self.request, obj)
        return obj

    def perform_create(self, serializer):
        project = Project.objects.get(id=self.project_id, workspace__slug=self.workspace_slug)
        # ProjectBaseModel.save() derives workspace from the project.
        serializer.save(project=project)

    def perform_destroy(self, instance):
        instance.delete()


class AutomationRuleToggleEndpoint(BaseAPIView):
    """POST: flip a rule's is_active flag. Project admins only (permission class)."""

    permission_classes = [ProjectBasePermission]

    def post(self, request, slug, project_id, rule_id):
        rule = get_object_or_404(
            AutomationRule,
            id=rule_id,
            project_id=project_id,
            workspace__slug=slug,
            deleted_at__isnull=True,
        )
        rule.is_active = not rule.is_active
        rule.save(update_fields=["is_active", "updated_at"])
        serializer = AutomationRuleSerializer(rule)
        return Response(serializer.data, status=status.HTTP_200_OK)
