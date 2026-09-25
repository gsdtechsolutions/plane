# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework.permissions import BasePermission, SAFE_METHODS

from plane.db.models import ProjectMember, WorkspaceMember


class InfraLinkPermission(BasePermission):
    """Project members may read infra links; writes need project admin (20)
    or workspace admin — mirrors AutomationRulePermission."""

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
