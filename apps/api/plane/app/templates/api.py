# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.response import Response

from plane.db.models import IssueTemplate, Project, ProjectMember, WorkspaceMember
from plane.app.templates.serializers import IssueTemplateSerializer
from plane.app.views.base import BaseAPIView, BaseViewSet


class IssueTemplatePermission(BasePermission):
    """List/retrieve/apply for any active project member; create/update/delete for project admins."""

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
        # Applying a template is a member-level action (like listing).
        if isinstance(view, IssueTemplateApplyEndpoint):
            return True
        return request.method in SAFE_METHODS or members.filter(role=20).exists() or workspace.filter(role=20).exists()


class IssueTemplateViewSet(BaseViewSet):
    """CRUD for per-project work item templates.

    Every query is bound to the workspace slug + project id from the URL, so a
    template from another project/workspace is never reachable (404, not 403).
    """

    serializer_class = IssueTemplateSerializer
    model = IssueTemplate
    permission_classes = [IssueTemplatePermission]

    def get_queryset(self):
        return IssueTemplate.objects.filter(
            workspace__slug=self.workspace_slug,
            project_id=self.project_id,
            deleted_at__isnull=True,
        ).order_by("-created_at")

    def get_object(self):
        queryset = self.get_queryset()
        template_id = self.kwargs.get("template_id")
        obj = get_object_or_404(queryset, id=template_id)
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


class IssueTemplateApplyEndpoint(BaseAPIView):
    """POST: resolve a template into a work item create-form prefill payload.

    Returns name/description/priority/state/label_ids/assignee_ids and a
    template-relative target date (today + due_in_days when set).
    """

    permission_classes = [IssueTemplatePermission]

    def post(self, request, slug, project_id, template_id):
        template = get_object_or_404(
            IssueTemplate.objects.filter(
                project_id=project_id,
                workspace__slug=slug,
                deleted_at__isnull=True,
            ).prefetch_related("labels", "assignees"),
            id=template_id,
        )
        target_date = (
            timezone.localdate() + timedelta(days=template.due_in_days) if template.due_in_days is not None else None
        )
        return Response(
            {
                "template_id": str(template.id),
                "name": template.name,
                "description_html": template.description_html,
                "description_json": template.description_json,
                "priority": template.priority,
                "state": str(template.state_id) if template.state_id else None,
                "label_ids": [str(label_id) for label_id in template.labels.values_list("id", flat=True)],
                "assignee_ids": [str(user_id) for user_id in template.assignees.values_list("id", flat=True)],
                "target_date": target_date.isoformat() if target_date else None,
            },
            status=status.HTTP_200_OK,
        )
