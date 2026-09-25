# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from urllib.parse import urlsplit

from rest_framework import serializers

from plane.app.serializers.base import BaseSerializer
from plane.db.models import InfraConnection, ProjectInfraLink
from plane.utils.ip_address import resolve_and_validate

SERVICE_BY_KIND = {
    "coolify_app": "coolify",
    "grafana_dashboard": "grafana",
}


class InfraConnectionSerializer(BaseSerializer):
    """Never exposes the API token: writable ``api_token`` goes in encrypted,
    readable responses carry only ``has_token``."""

    api_token = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)
    has_token = serializers.SerializerMethodField()

    class Meta:
        model = InfraConnection
        fields = ["id", "name", "service", "base_url", "has_token", "api_token", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at", "workspace"]

    def get_has_token(self, obj):
        return bool(obj.api_token_encrypted)

    def validate_service(self, value):
        if value not in {choice[0] for choice in InfraConnection.SERVICE_CHOICES}:
            raise serializers.ValidationError("Unsupported service.")
        return value

    def validate_base_url(self, value):
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise serializers.ValidationError("Base URL must be an absolute http(s) URL.")
        # Private subnets are the norm for self-hosted infra, so the host is
        # resolved in trusted mode (no safe-list check) — but it must resolve.
        try:
            resolve_and_validate(parts.hostname, require_safe=False)
        except ValueError as exc:
            raise serializers.ValidationError(f"Base URL host is not reachable from the API: {exc}")
        return value

    def _apply_token(self, validated_data):
        raw_token = validated_data.pop("api_token", None)
        # Sentinel: empty string means "clear the token", absent key means "leave it".
        return raw_token

    def create(self, validated_data):
        raw_token = self._apply_token(validated_data)
        instance = super().create(validated_data)
        instance.set_api_token(raw_token or "")
        instance.save(update_fields=["api_token_encrypted"])
        return instance

    def update(self, instance, validated_data):
        raw_token = self._apply_token(validated_data)
        instance = super().update(instance, validated_data)
        if raw_token is not None:  # only touch the token when the key was sent
            instance.set_api_token(raw_token)
            instance.save(update_fields=["api_token_encrypted"])
        return instance


class InfraLinkSerializer(BaseSerializer):
    connection_info = serializers.SerializerMethodField()

    class Meta:
        model = ProjectInfraLink
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

    def get_connection_info(self, obj):
        connection = obj.connection
        return {"id": str(connection.id), "name": connection.name, "service": connection.service}

    def _project(self):
        project = self.context.get("project", None)
        if project is None and self.instance is not None:
            project = self.instance.project
        if project is None:
            raise serializers.ValidationError({"project": "Project context is required."})
        return project

    def validate(self, attrs):
        project = self._project()
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        if kind not in {choice[0] for choice in ProjectInfraLink.KIND_CHOICES}:
            raise serializers.ValidationError({"kind": "Invalid link kind."})

        connection = attrs.get("connection", getattr(self.instance, "connection", None))
        if connection is None:
            raise serializers.ValidationError({"connection": "A workspace connection is required."})
        if connection.workspace_id != project.workspace_id or connection.deleted_at is not None:
            raise serializers.ValidationError({"connection": "Connection does not belong to this workspace."})
        if connection.service != SERVICE_BY_KIND[kind]:
            raise serializers.ValidationError(
                {"kind": f"Kind {kind} requires a {SERVICE_BY_KIND[kind]} connection."}
            )

        external_id = attrs.get("external_id", getattr(self.instance, "external_id", ""))
        external_url = attrs.get("external_url", getattr(self.instance, "external_url", ""))
        if kind == "coolify_app" and not external_id:
            raise serializers.ValidationError({"external_id": "A Coolify application uuid is required."})
        if kind == "grafana_dashboard" and not external_id and not external_url:
            raise serializers.ValidationError(
                {"external_url": "Provide a dashboard uid or an explicit dashboard URL."}
            )

        # Discovered dashboards always get a canonical absolute URL; manual
        # links keep whatever URL the admin pasted.
        if kind == "grafana_dashboard" and external_id and connection.service == "grafana":
            base = connection.base_url.rstrip("/")
            attrs["external_url"] = f"{base}/d/{external_id}"
        if not attrs.get("display_name") and external_id and kind == "grafana_dashboard":
            attrs["display_name"] = external_id
        return attrs
