# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db.models import Q
from rest_framework import serializers

from plane.db.models import IssueTemplate, Label, ProjectMember, State
from plane.app.serializers.base import BaseSerializer


class IssueTemplateSerializer(BaseSerializer):
    class Meta:
        model = IssueTemplate
        fields = "__all__"
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "workspace",
            "project",
            "created_by",
            "updated_by",
        ]

    def _project(self):
        """Project context for relational validation (the view injects it; updates fall back to the instance)."""
        project = self.context.get("project", None)
        if project is None and self.instance is not None:
            project = self.instance.project
        if project is None:
            raise serializers.ValidationError({"project": "Project context is required."})
        return project

    def validate_name(self, value):
        if not value or not str(value).strip():
            raise serializers.ValidationError("Name is required.")
        return value

    def validate_priority(self, value):
        allowed = {choice[0] for choice in IssueTemplate.PRIORITY_CHOICES}
        if value not in allowed:
            raise serializers.ValidationError(f"Invalid priority: {value}.")
        return value

    def validate_description_json(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("description_json must be a JSON object.")
        return value

    def validate_due_in_days(self, value):
        if value is None:
            return None
        if value < 0:
            raise serializers.ValidationError("due_in_days cannot be negative.")
        return value

    def validate_state(self, value):
        if value is None:
            return None
        project = self._project()
        if not State.objects.filter(id=value.id, project_id=project.id, deleted_at__isnull=True).exists():
            raise serializers.ValidationError("State does not belong to this project.")
        return value

    def validate_labels(self, value):
        project = self._project()
        # Project labels, or workspace-level labels usable from the project.
        valid = Label.objects.filter(
            Q(project_id=project.id) | Q(project__isnull=True, workspace_id=project.workspace_id),
            deleted_at__isnull=True,
        )
        invalid = [label.id for label in value if not valid.filter(id=label.id).exists()]
        if invalid:
            raise serializers.ValidationError("Labels must belong to this project (or its workspace).")
        return value

    def validate_assignees(self, value):
        project = self._project()
        invalid = [
            user.id
            for user in value
            if not ProjectMember.objects.filter(
                member_id=user.id, project_id=project.id, is_active=True, role__gte=15
            ).exists()
        ]
        if invalid:
            raise serializers.ValidationError("Assignees must be active members of this project.")
        return value
