# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Serializers for the Asana sync API.

The PAT is write-only and accepted only on create (rotation replaces the
whole connection record); it is never returned in any response.
"""

# Python imports
import re

# Third party imports
from rest_framework import serializers

# Module imports
from plane.app.asana_sync.crypto import encrypt_token
from plane.db.models import AsanaConnection, AsanaProjectSync, AsanaSyncLog, State

_GID_RE = re.compile(r"^\d{6,32}$")


class AsanaConnectionSerializer(serializers.ModelSerializer):
    personal_access_token = serializers.CharField(write_only=True, max_length=255)
    pat_preview = serializers.SerializerMethodField()

    class Meta:
        model = AsanaConnection
        fields = [
            "id",
            "name",
            "personal_access_token",
            "pat_preview",
            "asana_workspace_gid",
            "asana_workspace_name",
            "is_active",
            "last_verified_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "pat_preview",
            "asana_workspace_gid",
            "asana_workspace_name",
            "last_verified_at",
            "created_at",
            "updated_at",
        ]

    def get_pat_preview(self, obj) -> str:
        return f"Asana PAT •••• {obj.id.hex[-4:]}" if obj.pat_encrypted else ""

    def validate_personal_access_token(self, value: str) -> str:
        token = value.strip()
        # Asana PATs are `1/<digits>` or long numeric strings; keep validation
        # permissive enough for enterprise proxies but reject obvious junk.
        if len(token) < 20:
            raise serializers.ValidationError("That does not look like an Asana personal access token.")
        return token

    def create(self, validated_data):
        token = validated_data.pop("personal_access_token")
        return AsanaConnection.objects.create(pat_encrypted=encrypt_token(token), **validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("personal_access_token", None)  # rotate = delete + reconnect
        return super().update(instance, validated_data)


class AsanaProjectSyncSerializer(serializers.ModelSerializer):
    class Meta:
        model = AsanaProjectSync
        fields = [
            "id",
            "connection",
            "asana_project_gid",
            "asana_project_name",
            "direction",
            "sync_subtasks",
            "sync_comments",
            "state_map",
            "default_state_id",
            "label_map",
            "assignee_map",
            "webhook_configured",
            "initial_sync_done",
            "last_synced_at",
            "is_active",
            "assignee_property_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "state_map",
            "label_map",
            "assignee_map",
            "webhook_configured",
            "initial_sync_done",
            "last_synced_at",
            "assignee_property_id",
            "created_at",
            "updated_at",
        ]

    def validate_asana_project_gid(self, value: str) -> str:
        if not _GID_RE.match(value or ""):
            raise serializers.ValidationError("Invalid Asana project GID.")
        return value

    def validate_default_state_id(self, value):
        if value is None:
            return value
        project = self.context.get("project")
        if project and not State.objects.filter(id=value, project_id=project.id).exists():
            raise serializers.ValidationError("State does not belong to this project.")
        return value

    def validate(self, attrs):
        connection = attrs.get("connection")
        if connection is not None and self.context.get("workspace_id"):
            if str(connection.workspace_id) != str(self.context["workspace_id"]):
                raise serializers.ValidationError({"connection": "Connection belongs to a different workspace."})
        return attrs


class AsanaSyncLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AsanaSyncLog
        fields = [
            "id",
            "sync",
            "direction",
            "entity_type",
            "entity_gid",
            "issue",
            "status",
            "message",
            "detail",
            "created_at",
        ]
        read_only_fields = fields
