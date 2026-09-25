# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Serializers and type-schema helpers for per-project custom work-item properties.

The canonical settings_json shape (also the shape returned to clients):
- every type: {"required": bool, "description": str}
- select: additionally {"options": [{"id": str(uuid), "name": str, "color": str}]}
- number: additionally {"unit": str}

Value coercion/validation for the issue values map lives here too so the
property-def serializer and the values endpoint share one source of truth.
"""

import re
import uuid
from datetime import date, datetime

# Django imports
from django.utils.dateparse import parse_date
from rest_framework import serializers

# Module imports
from plane.db.models import CustomProperty
from plane.app.serializers.base import BaseSerializer

DEFAULT_OPTION_COLOR = "#6b7280"
MAX_DESCRIPTION_LENGTH = 1000
MAX_UNIT_LENGTH = 32
MAX_OPTION_NAME_LENGTH = 255
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


class CustomPropertySerializer(BaseSerializer):
    """CRUD serializer for CustomProperty definitions."""

    class Meta:
        model = CustomProperty
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

    def validate_name(self, value):
        if not isinstance(value, str) or not value.strip():
            raise serializers.ValidationError("Name is required.")
        return value.strip()

    def validate_type(self, value):
        allowed = {choice[0] for choice in CustomProperty.PROPERTY_TYPE_CHOICES}
        if value not in allowed:
            raise serializers.ValidationError(f"Unknown property type: {value}.")
        return value

    def _project(self):
        project = self.context.get("project", None)
        if project is None and self.instance is not None:
            project = self.instance.project
        if project is None:
            raise serializers.ValidationError({"project": "Project context is required."})
        return project

    def validate(self, attrs):
        project = self._project()
        # Case-insensitive duplicate name check (mirror of model.clean, serializer-shaped).
        name = attrs.get("name", getattr(self.instance, "name", None))
        existing = CustomProperty.objects.filter(
            project_id=project.id,
            name__iexact=name,
            deleted_at__isnull=True,
        )
        if self.instance is not None:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError({"name": "A property with this name already exists in this project."})

        prop_type = attrs.get("type", getattr(self.instance, "type", None))
        # Normalize on create and whenever settings/type are being edited; a
        # PATCH that only flips is_active keeps the stored settings untouched.
        if "settings_json" in attrs or self.instance is None:
            current = attrs.get("settings_json", getattr(self.instance, "settings_json", None))
            attrs["settings_json"] = normalize_settings_json(prop_type, current if current is not None else {})
        return attrs


def normalize_settings_json(prop_type, settings_json):
    """Validate + canonicalize the settings_json payload for a property type.

    Raises serializers.ValidationError on shape violations; returns a normalized
    dict that is always safe to persist and render.
    """
    if not isinstance(settings_json, dict):
        raise serializers.ValidationError({"settings_json": "Settings must be an object."})

    normalized: dict = {
        "required": bool(settings_json.get("required", False)),
    }
    description = settings_json.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise serializers.ValidationError({"settings_json": "description must be a string."})
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise serializers.ValidationError(
            {"settings_json": f"description must be at most {MAX_DESCRIPTION_LENGTH} characters."}
        )
    normalized["description"] = description

    if prop_type == "number":
        unit = settings_json.get("unit", "")
        if unit is None:
            unit = ""
        if not isinstance(unit, str):
            raise serializers.ValidationError({"settings_json": "unit must be a string."})
        if len(unit) > MAX_UNIT_LENGTH:
            raise serializers.ValidationError({"settings_json": f"unit must be at most {MAX_UNIT_LENGTH} characters."})
        normalized["unit"] = unit

    if prop_type == "select":
        raw_options = settings_json.get("options", [])
        if not isinstance(raw_options, list) or len(raw_options) == 0:
            raise serializers.ValidationError({"settings_json": "Select properties require at least one option."})
        seen_names = set()
        options = []
        for raw in raw_options:
            if not isinstance(raw, dict):
                raise serializers.ValidationError({"settings_json": "Each option must be an object."})
            option_name = raw.get("name")
            if not isinstance(option_name, str) or not option_name.strip():
                raise serializers.ValidationError({"settings_json": "Each option requires a non-empty name."})
            option_name = option_name.strip()
            if len(option_name) > MAX_OPTION_NAME_LENGTH:
                raise serializers.ValidationError({"settings_json": "Option names must be at most 255 characters."})
            if option_name.lower() in seen_names:
                raise serializers.ValidationError({"settings_json": "Option names must be unique (case-insensitive)."})
            seen_names.add(option_name.lower())
            color = raw.get("color", DEFAULT_OPTION_COLOR)
            if color is None or color == "":
                color = DEFAULT_OPTION_COLOR
            if not isinstance(color, str) or not _HEX_COLOR_RE.match(color):
                raise serializers.ValidationError({"settings_json": f"Invalid option color: {color}."})
            option_id = raw.get("id")
            if option_id in (None, ""):
                option_id = str(uuid.uuid4())
            # Accept any client-supplied id string; normalize to str for JSON stability.
            options.append({"id": str(option_id), "name": option_name, "color": color})
        normalized["options"] = options

    return normalized


def option_ids_for(prop: CustomProperty):
    """Option ids declared by a select property's settings_json."""
    if prop.type != "select":
        return []
    options = (prop.settings_json or {}).get("options") or []
    return [str(option.get("id")) for option in options if isinstance(option, dict) and option.get("id") is not None]


class ValueTypeError(Exception):
    """Raised when a client value cannot be coerced to the property's type."""


def coerce_value(prop: CustomProperty, value):
    """Coerce a client value for ``prop`` into its typed storage representation.

    Returns the normalized client-facing value:
    - text: str | None
    - number: float | None
    - date: "YYYY-MM-DD" | None
    - checkbox: bool | None
    - select: list[str] of option ids (possibly empty)

    Raises ValueTypeError when the value cannot be interpreted.
    """
    if prop.type == "select":
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        option_ids = option_ids_for(prop)
        allowed = set(option_ids)
        result = []
        for item in value:
            item = str(item) if item is not None else ""
            if item == "":
                continue
            if item not in allowed:
                raise ValueTypeError("Value does not match any option of this property.")
            if item not in result:
                result.append(item)
        return result

    if value is None or value == "":
        return None

    if prop.type == "text":
        if isinstance(value, (dict, list)):
            raise ValueTypeError("Value must be a string.")
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    if prop.type == "number":
        if isinstance(value, bool):
            raise ValueTypeError("Value must be a number.")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                raise ValueTypeError("Value must be a number.")
        raise ValueTypeError("Value must be a number.")

    if prop.type == "date":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            parsed = parse_date(value.strip())
            if parsed is None:
                raise ValueTypeError("Value must be an ISO date (YYYY-MM-DD).")
            return parsed.isoformat()
        raise ValueTypeError("Value must be an ISO date (YYYY-MM-DD).")

    if prop.type == "checkbox":
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "yes", "1"):
                return True
            if lowered in ("false", "no", "0"):
                return False
        raise ValueTypeError("Value must be a boolean.")

    raise ValueTypeError(f"Unknown property type: {prop.type}.")


def storage_fields_for(prop: CustomProperty, normalized_value):
    """Map a normalized value onto the CustomPropertyValue storage columns."""
    if prop.type == "text":
        return {"value_text": normalized_value if normalized_value is not None else ""}
    if prop.type == "number":
        return {"value_number": normalized_value}
    if prop.type == "date":
        # Store as date object when present, else null.
        from django.utils.dateparse import parse_date as _parse_date

        return {"value_date": _parse_date(normalized_value) if normalized_value else None}
    if prop.type == "checkbox":
        return {"value_bool": normalized_value}
    if prop.type == "select":
        return {"value_option": normalized_value}
    raise ValueTypeError(f"Unknown property type: {prop.type}.")


def read_value(prop: CustomProperty, value_row) -> object:
    """Read the typed client-facing value out of a CustomPropertyValue row (or None)."""
    if value_row is None:
        return None
    if prop.type == "text":
        return value_row.value_text if value_row.value_text != "" else None
    if prop.type == "number":
        return value_row.value_number
    if prop.type == "date":
        return value_row.value_date.isoformat() if value_row.value_date is not None else None
    if prop.type == "checkbox":
        return value_row.value_bool
    if prop.type == "select":
        options = value_row.value_option or []
        # Drop options that were removed from settings after the value was saved.
        allowed = set(option_ids_for(prop))
        return [option_id for option_id in options if option_id in allowed]
    return None
