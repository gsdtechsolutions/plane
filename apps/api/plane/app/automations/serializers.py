# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from datetime import date

from django.db.models import Q
from rest_framework import serializers

from plane.db.models import AutomationRule, Issue, Label, ProjectMember, State
from plane.app.serializers.base import BaseSerializer


class AutomationRuleSerializer(BaseSerializer):
    class Meta:
        model = AutomationRule
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

    def _validate_trigger_value(self, trigger_type, trigger_value):
        if trigger_value in (None, ""):
            return None
        project = self._project()
        if project is not None:
            if trigger_type == "state_changed":
                if not State.objects.filter(id=trigger_value, project_id=project.id, deleted_at__isnull=True).exists():
                    raise serializers.ValidationError({"trigger_value": "State does not belong to this project."})
            elif trigger_type == "assignee_added":
                if not ProjectMember.objects.filter(
                    member_id=trigger_value, project_id=project.id, is_active=True, role__gte=15
                ).exists():
                    raise serializers.ValidationError(
                        {"trigger_value": "User is not an active member of this project."}
                    )
        return trigger_value

    def _validate_actions(self, actions):
        if not isinstance(actions, list) or len(actions) == 0:
            raise serializers.ValidationError({"actions": "At least one action is required."})
        allowed_types = {choice[0] for choice in AutomationRule.ACTION_TYPE_CHOICES}
        valid_priorities = {choice[0] for choice in Issue.PRIORITY_CHOICES}
        project = self._project()

        for action in actions:
            if not isinstance(action, dict):
                raise serializers.ValidationError({"actions": "Each action must be an object with type and value."})
            action_type = action.get("type")
            action_value = action.get("value")
            if not isinstance(action_type, str) or action_type not in allowed_types:
                raise serializers.ValidationError({"actions": f"Unknown action type: {action_type}."})
            if action_value in (None, ""):
                raise serializers.ValidationError({"actions": f"Action {action_type} requires a value."})
            if action_type in ("set_state", "add_label", "remove_label", "assign_member"):
                action_value = serializers.UUIDField().run_validation(action_value)
            elif not isinstance(action_value, str):
                raise serializers.ValidationError({"actions": "Action value must be a string."})
            if action_type == "set_state":
                if not State.objects.filter(id=action_value, project_id=project.id, deleted_at__isnull=True).exists():
                    raise serializers.ValidationError({"actions": "set_state references a state outside this project."})
            elif action_type in ("add_label", "remove_label"):
                # Project labels, or workspace-level labels usable from the project.
                if not Label.objects.filter(
                    Q(project_id=project.id) | Q(project__isnull=True, workspace_id=project.workspace_id),
                    id=action_value,
                    deleted_at__isnull=True,
                ).exists():
                    raise serializers.ValidationError(
                        {"actions": "Label action references a label outside this project."}
                    )
            elif action_type == "assign_member":
                if not ProjectMember.objects.filter(
                    member_id=action_value, project_id=project.id, is_active=True, role__gte=15
                ).exists():
                    raise serializers.ValidationError(
                        {"actions": "assign_member references a user who is not an active project member."}
                    )
            elif action_type == "set_priority":
                if action_value not in valid_priorities:
                    raise serializers.ValidationError({"actions": f"Invalid priority: {action_value}."})
            elif action_type == "set_due_date":
                try:
                    date.fromisoformat(str(action_value))
                except (TypeError, ValueError):
                    raise serializers.ValidationError({"actions": "set_due_date requires an ISO date (YYYY-MM-DD)."})
        return actions

    def validate(self, attrs):
        # Operational changes must remain possible when referenced resources
        # have been removed. Configuration edits and enabling still validate.
        validate_configuration = (
            self.instance is None
            or bool({"trigger_type", "trigger_value", "actions"}.intersection(attrs))
            or attrs.get("is_active") is True
        )
        if not validate_configuration:
            return attrs
        trigger_type = attrs.get("trigger_type", getattr(self.instance, "trigger_type", None))
        trigger_value = attrs.get("trigger_value", getattr(self.instance, "trigger_value", None))
        if trigger_type not in {choice[0] for choice in AutomationRule.TRIGGER_TYPE_CHOICES}:
            raise serializers.ValidationError({"trigger_type": "Invalid trigger type."})
        attrs["trigger_value"] = self._validate_trigger_value(trigger_type, trigger_value)
        if "actions" in attrs:
            attrs["actions"] = self._validate_actions(attrs["actions"])
        elif self.instance is None:
            raise serializers.ValidationError({"actions": "This field is required."})
        elif attrs.get("is_active") is True:
            self._validate_actions(self.instance.actions)
        return attrs
