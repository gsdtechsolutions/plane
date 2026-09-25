# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""API endpoints for per-project custom work-item properties.

Routes (registered in plane.app.urls.customproperties):
- GET/POST    .../custom-properties/                       (defs list/create)
- GET/PUT/PATCH/DELETE .../custom-properties/<property_id>/ (defs detail)
- POST        .../custom-properties/reorder/               ({ordered_ids: [...]})
- GET/PUT     .../issues/<issue_id>/custom-property-values/ (typed values map)

Values map contract ({property_id: value}):
- text: str | null
- number: number | null
- date: "YYYY-MM-DD" | null
- checkbox: bool | null
- select: string[] of option ids (single-select: 0 or 1 entries; [] = unanswered)

PUT is a full replace over the project's ACTIVE properties: keys missing from
the map are cleared (deletion = missing keys), unknown/inactive ids are a 400.
"""

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response

# Module imports
from plane.app.customproperties.serializers import (
    CustomPropertySerializer,
    ValueTypeError,
    coerce_value,
    read_value,
    storage_fields_for,
)
from plane.app.views.base import BaseAPIView, BaseViewSet
from plane.db.models import CustomProperty, CustomPropertyValue, Issue, Project, ProjectMember, WorkspaceMember


class CustomPropertyPermission(BasePermission):
    """Property definitions: project/workspace admins write, members read."""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        workspace_member = WorkspaceMember.objects.filter(
            workspace__slug=view.workspace_slug,
            member=request.user,
            is_active=True,
        )
        project_member = ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            project_id=view.project_id,
            member=request.user,
            is_active=True,
        )
        if not workspace_member.exists() or not project_member.exists():
            return False
        if request.method in SAFE_METHODS:
            return True
        return project_member.filter(role=20).exists() or workspace_member.filter(role=20).exists()


class IssueCustomPropertyValuePermission(BasePermission):
    """Issue values: members read, members+ (role >= 15) write."""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        workspace_member = WorkspaceMember.objects.filter(
            workspace__slug=view.workspace_slug,
            member=request.user,
            is_active=True,
        )
        project_member = ProjectMember.objects.filter(
            workspace__slug=view.workspace_slug,
            project_id=view.project_id,
            member=request.user,
            is_active=True,
        )
        if not workspace_member.exists() or not project_member.exists():
            return False
        if request.method in SAFE_METHODS:
            return True
        return project_member.filter(role__gte=15).exists() or workspace_member.filter(role=20).exists()


class CustomPropertyViewSet(BaseViewSet):
    """CRUD for typed custom property definitions of one project."""

    serializer_class = CustomPropertySerializer
    model = CustomProperty
    permission_classes = [CustomPropertyPermission]

    def get_queryset(self):
        return (
            CustomProperty.objects.filter(
                workspace__slug=self.workspace_slug,
                project_id=self.project_id,
                deleted_at__isnull=True,
            )
            .order_by("sort_order", "created_at")
        )

    def get_object(self):
        queryset = self.get_queryset()
        obj = get_object_or_404(queryset, id=self.kwargs.get("property_id"))
        self.check_object_permissions(self.request, obj)
        return obj

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["project"] = get_object_or_404(
            Project,
            id=self.project_id,
            workspace__slug=self.workspace_slug,
        )
        return context

    def perform_create(self, serializer):
        project = Project.objects.get(id=self.project_id, workspace__slug=self.workspace_slug)
        # ProjectBaseModel.save() derives workspace from the project.
        if not serializer.validated_data.get("sort_order"):
            max_order = self.get_queryset().order_by("-sort_order").values_list("sort_order", flat=True).first()
            serializer.validated_data["sort_order"] = (max_order + 1) if max_order is not None else 0
        serializer.save(project=project)

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        instance.delete()


class CustomPropertyReorderEndpoint(BaseAPIView):
    """POST: persist a new display order for the project's property definitions.

    Body: {"ordered_ids": [property_id, ...]} — must list every non-deleted
    property of the project exactly once.
    """

    permission_classes = [CustomPropertyPermission]

    @transaction.atomic
    def post(self, request, slug, project_id):
        ordered_ids = request.data.get("ordered_ids")
        if not isinstance(ordered_ids, list) or not all(isinstance(item, str) for item in ordered_ids):
            return Response(
                {"ordered_ids": "A list of property ids is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        properties = list(
            CustomProperty.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                deleted_at__isnull=True,
            )
        )
        if sorted(ordered_ids) != sorted(str(prop.id) for prop in properties):
            return Response(
                {"ordered_ids": "ordered_ids must contain every property of the project exactly once."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        order_by_id = {str(prop.id): index for index, prop in enumerate(properties)}
        for index, property_id in enumerate(ordered_ids):
            prop = next(prop for prop in properties if str(prop.id) == property_id)
            if prop.sort_order != index:
                prop.sort_order = index
                prop.save(update_fields=["sort_order", "updated_at"])
        serializer = CustomPropertySerializer(
            sorted(properties, key=lambda prop: order_by_id[str(prop.id)]),
            many=True,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class IssueCustomPropertyValuesEndpoint(BaseAPIView):
    """GET/PUT the typed custom property values of one issue."""

    permission_classes = [IssueCustomPropertyValuePermission]

    def _active_properties(self, slug, project_id):
        return list(
            CustomProperty.objects.filter(
                workspace__slug=slug,
                project_id=project_id,
                is_active=True,
                deleted_at__isnull=True,
            ).order_by("sort_order", "created_at")
        )

    def _build_response(self, slug, project_id, issue_id, properties):
        rows = {
            row.property_id: row
            for row in CustomPropertyValue.objects.filter(
                issue_id=issue_id,
                property__in=[prop.id for prop in properties],
                deleted_at__isnull=True,
            )
        }
        property_values = {str(prop.id): read_value(prop, rows.get(prop.id)) for prop in properties}
        return {
            "properties": CustomPropertySerializer(properties, many=True).data,
            "property_values": property_values,
        }

    def get(self, request, slug, project_id, issue_id):
        issue = get_object_or_404(
            Issue,
            id=issue_id,
            project_id=project_id,
            workspace__slug=slug,
        )
        properties = self._active_properties(slug, project_id)
        return Response(self._build_response(slug, project_id, issue.id, properties), status=status.HTTP_200_OK)

    @transaction.atomic
    def put(self, request, slug, project_id, issue_id):
        issue = get_object_or_404(
            Issue,
            id=issue_id,
            project_id=project_id,
            workspace__slug=slug,
        )
        payload = request.data
        if isinstance(payload, dict) and "property_values" in payload:
            values_map = payload.get("property_values")
        else:
            # Tolerate a bare {property_id: value} map.
            values_map = payload
        if not isinstance(values_map, dict):
            return Response(
                {"property_values": "A map of {property_id: value} is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        properties = self._active_properties(slug, project_id)
        properties_by_id = {str(prop.id): prop for prop in properties}

        # Validate all keys up front: unknown or inactive ids are rejected.
        unknown_ids = [key for key in values_map if key not in properties_by_id]
        if unknown_ids:
            return Response(
                {"property_values": {key: "Unknown or inactive property." for key in unknown_ids}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Coerce + validate every provided value before touching the database.
        coerced: dict = {}
        errors: dict = {}
        for property_id, raw_value in values_map.items():
            prop = properties_by_id[property_id]
            try:
                coerced[property_id] = coerce_value(prop, raw_value)
            except ValueTypeError as exc:
                errors[property_id] = str(exc)
        if errors:
            return Response({"property_values": errors}, status=status.HTTP_400_BAD_REQUEST)

        # Full replace over active properties: missing keys are cleared.
        for prop in properties:
            property_id = str(prop.id)
            normalized = coerced.get(property_id)
            is_empty = normalized is None or normalized == [] or normalized == ""
            existing = CustomPropertyValue.objects.filter(
                property=prop,
                issue=issue,
                deleted_at__isnull=True,
            ).first()
            if is_empty:
                if existing is not None:
                    existing.delete()
                continue
            fields = storage_fields_for(prop, normalized)
            if existing is None:
                CustomPropertyValue.objects.create(property=prop, issue=issue, **fields)
            else:
                for field_name, field_value in fields.items():
                    setattr(existing, field_name, field_value)
                existing.save(update_fields=[*fields.keys(), "updated_at"])

        return Response(self._build_response(slug, project_id, issue.id, properties), status=status.HTTP_200_OK)
