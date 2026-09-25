# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.core.exceptions import ValidationError as DjangoValidationError

# Third party imports
from rest_framework import serializers

# Module imports
from plane.affine_sync.client import AffineError, normalize_instance_url, validate_api_token
from plane.db.models import AffineConnection, AffinePageMap, Page, Project


class AffineConnectionSerializer(serializers.ModelSerializer):
    """Read shape: token is never serialized back out."""

    project_detail = serializers.SerializerMethodField()
    page_count = serializers.SerializerMethodField()
    conflict_count = serializers.SerializerMethodField()

    class Meta:
        model = AffineConnection
        fields = [
            "id",
            "workspace",
            "project",
            "project_detail",
            "affine_instance_url",
            "affine_workspace_id",
            "affine_workspace_name",
            "settings",
            "is_active",
            "last_synced_at",
            "last_sync_status",
            "last_sync_error",
            "page_count",
            "conflict_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["workspace", "last_synced_at", "last_sync_status", "last_sync_error"]

    def get_project_detail(self, obj):
        return {
            "id": str(obj.project_id),
            "name": obj.project.name if obj.project else "",
            "identifier": obj.project.identifier if obj.project else "",
        }

    def get_page_count(self, obj):
        return obj.page_maps.filter(deleted_at__isnull=True).count()

    def get_conflict_count(self, obj):
        return obj.page_maps.filter(deleted_at__isnull=True, status="conflict").count()


class AffineConnectionCreateSerializer(AffineConnectionSerializer):
    """Write shape: accepts api_token + instance URL, validates both live."""

    api_token = serializers.CharField(write_only=True, trim_whitespace=True)

    class Meta(AffineConnectionSerializer.Meta):
        fields = AffineConnectionSerializer.Meta.fields + ["api_token"]
        read_only_fields = AffineConnectionSerializer.Meta.read_only_fields

    def validate_affine_instance_url(self, value):
        try:
            return normalize_instance_url(value)
        except AffineError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_api_token(self, value):
        try:
            return validate_api_token(value)
        except AffineError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_project(self, value):
        request = self.context.get("request")
        if value is None:
            raise serializers.ValidationError("A project is required.")
        if request is not None and not Project.objects.filter(pk=value.pk, workspace__slug=self.context["workspace_slug"]).exists():
            raise serializers.ValidationError("Project does not belong to this workspace.")
        return value

    def validate(self, attrs):
        # live probe: only save credentials the AFFiNE instance actually accepts
        from plane.affine_sync.engine import verify_and_list_workspaces

        instance_url = attrs.get("affine_instance_url") or getattr(self.instance, "affine_instance_url", None)
        token = attrs.get("api_token") or getattr(self.instance, "api_token", None)
        workspace_id = attrs.get("affine_workspace_id") or getattr(self.instance, "affine_workspace_id", None)
        try:
            workspaces = verify_and_list_workspaces(instance_url, token)
        except AffineError as exc:
            raise serializers.ValidationError({"affine_instance_url": str(exc)})
        valid_ids = {ws["id"] for ws in workspaces}
        if workspace_id and valid_ids and workspace_id not in valid_ids:
            raise serializers.ValidationError({"affine_workspace_id": "Selected AFFiNE workspace is not accessible with this token."})
        for ws in workspaces:
            if ws["id"] == workspace_id:
                attrs["affine_workspace_name"] = ws["name"][:255]
                break
        return attrs

    def create(self, validated_data):
        validated_data.pop("affine_workspace_name", None)  # re-derived below
        # probe again (validate() can't mutate name when workspace_id newly valid)
        from plane.affine_sync.engine import verify_and_list_workspaces

        name = ""
        try:
            for ws in verify_and_list_workspaces(validated_data["affine_instance_url"], validated_data["api_token"]):
                if ws["id"] == validated_data["affine_workspace_id"]:
                    name = ws["name"][:255]
                    break
        except AffineError:
            pass
        validated_data["affine_workspace_name"] = name
        return super().create(validated_data)


class AffineConnectionUpdateSerializer(AffineConnectionCreateSerializer):
    """Partial updates: api_token optional (keeps stored token when blank/absent)."""

    api_token = serializers.CharField(write_only=True, trim_whitespace=True, required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs.get("api_token") == "":
            attrs.pop("api_token")  # empty means "keep existing"
        return super().validate(attrs)


class AffineWorkspaceListRequestSerializer(serializers.Serializer):
    """Probe request body for listing AFFiNE workspaces before connecting."""

    instance_url = serializers.CharField(trim_whitespace=True)
    api_token = serializers.CharField(trim_whitespace=True)

    def validate_instance_url(self, value):
        try:
            return normalize_instance_url(value)
        except AffineError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_api_token(self, value):
        try:
            return validate_api_token(value)
        except AffineError as exc:
            raise serializers.ValidationError(str(exc))


class AffinePageMapSerializer(serializers.ModelSerializer):
    page_detail = serializers.SerializerMethodField()

    class Meta:
        model = AffinePageMap
        fields = [
            "id",
            "page",
            "page_detail",
            "affine_doc_id",
            "affine_doc_title",
            "affine_updated_at",
            "last_synced_at",
            "last_sync_direction",
            "status",
            "last_error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_page_detail(self, obj):
        page = obj.page
        return {
            "id": str(page.id) if page else None,
            "name": page.name if page else "",
            "archived_at": page.archived_at if page else None,
            "updated_at": page.updated_at if page else None,
        }
