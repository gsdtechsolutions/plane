# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Fork feature: per-project typed custom work-item properties.

# Django imports
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel
from .project import ProjectBaseModel


class CustomProperty(ProjectBaseModel):
    """A typed custom field definition scoped to a project.

    Type-specific configuration lives in ``settings_json``:
    - select: ``{"options": [{"id", "name", "color"}]}``
    - number: ``{"unit": ""}``
    - every type: ``{"required": bool, "description": str}``
    """

    PROPERTY_TYPE_CHOICES = [
        ("text", "Text"),
        ("number", "Number"),
        ("date", "Date"),
        ("select", "Select"),
        ("checkbox", "Checkbox"),
    ]

    # Override ProjectBaseModel's default related_name ("project_%(class)s").
    project = models.ForeignKey(
        "db.Project",
        on_delete=models.CASCADE,
        related_name="custom_properties",
    )
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=PROPERTY_TYPE_CHOICES, default="text")
    settings_json = models.JSONField(default=dict)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Custom Property"
        verbose_name_plural = "Custom Properties"
        db_table = "custom_properties"
        ordering = ("sort_order", "created_at")

    def __str__(self):
        return f"{self.project.name} - {self.name} ({self.type})"

    def clean(self):
        # Case-insensitive duplicate name check within the project (excluding self).
        # Deliberately not a DB constraint: soft-deleted rows must not block reuse.
        if self.name is None:
            return
        duplicate = CustomProperty.objects.filter(
            project_id=self.project_id,
            name__iexact=self.name,
            deleted_at__isnull=True,
        ).exclude(pk=self.pk)
        if duplicate.exists():
            raise ValidationError({"name": "A property with this name already exists in this project."})


class CustomPropertyValue(BaseModel):
    """The stored value of one CustomProperty on one issue.

    Only the column matching the property's type is meaningful; the typed API
    layer (plane.app.customproperties) validates and coerces before persisting.
    """

    property = models.ForeignKey(
        CustomProperty,
        on_delete=models.CASCADE,
        related_name="values",
    )
    issue = models.ForeignKey(
        "db.Issue",
        on_delete=models.CASCADE,
        related_name="custom_property_values",
    )
    value_text = models.TextField(blank=True, default="")
    value_number = models.FloatField(null=True)
    value_date = models.DateField(null=True)
    value_bool = models.BooleanField(null=True)
    # Multi-select stores option ids; a single-select stores a one-element list.
    value_option = models.JSONField(default=list)

    class Meta:
        verbose_name = "Custom Property Value"
        verbose_name_plural = "Custom Property Values"
        db_table = "custom_property_values"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["property", "issue"],
                condition=Q(deleted_at__isnull=True),
                name="custom_property_value_unique_property_issue_when_deleted_at_null",
            )
        ]

    def __str__(self):
        return f"{self.property.name} - {self.issue_id}"
