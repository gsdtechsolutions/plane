# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# ORCA PORT: workspace-level shared labels & states (taxonomy).
# Squashes orca-ref migrations 0123, 0124, 0125 and 0126 into a single clean
# migration representing the final schema (the intermediate WorkspaceProjectLabel
# table and the initial draft/planning ProjectState group choices never shipped).

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0122_alter_draftissue_assignees_alter_issue_assignees_and_more"),
    ]

    operations = [
        # ------------------------------------------------------------------
        # Workspace-level project states
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ProjectState",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=255, verbose_name="Project State Name")),
                ("description", models.TextField(blank=True, verbose_name="Project State Description")),
                ("color", models.CharField(max_length=255, verbose_name="Project State Color")),
                ("slug", models.SlugField(blank=True, max_length=100)),
                ("sequence", models.FloatField(default=65535)),
                (
                    "group",
                    models.CharField(
                        choices=[
                            ("backlog", "Backlog"),
                            ("unstarted", "Unstarted"),
                            ("started", "Started"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="backlog",
                        max_length=20,
                    ),
                ),
                ("default", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="project_states", to="db.workspace"
                    ),
                ),
            ],
            options={
                "verbose_name": "Project State",
                "verbose_name_plural": "Project States",
                "db_table": "project_states",
                "ordering": ("sequence",),
            },
        ),
        migrations.CreateModel(
            name="WorkspaceProjectStateSettings",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("is_enabled", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "workspace",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_state_settings",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Workspace Project State Settings",
                "verbose_name_plural": "Workspace Project State Settings",
                "db_table": "workspace_project_state_settings",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ProjectStateProperty",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("is_enabled", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "project",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="state_property",
                        to="db.project",
                    ),
                ),
                (
                    "state",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="projects",
                        to="db.projectstate",
                    ),
                ),
            ],
            options={
                "verbose_name": "Project State Property",
                "verbose_name_plural": "Project State Properties",
                "db_table": "project_state_properties",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="projectstate",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("name", "workspace"),
                name="project_state_unique_name_workspace_when_deleted_at_null",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="projectstate",
            unique_together={("name", "workspace", "deleted_at")},
        ),
        # ------------------------------------------------------------------
        # Workspace-level shared labels + project mappings
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ProjectProjectLabel",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "label",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_label_mappings",
                        to="db.label",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_project_labels",
                        to="db.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "Project Project Label",
                "verbose_name_plural": "Project Project Labels",
                "db_table": "project_project_labels",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="WorkspaceProjectLabelSettings",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("is_enabled", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "workspace",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_label_settings",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Workspace Project Label Settings",
                "verbose_name_plural": "Workspace Project Label Settings",
                "db_table": "workspace_project_label_settings",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="ProjectLabelProperty",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("is_enabled", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "project",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="label_property",
                        to="db.project",
                    ),
                ),
            ],
            options={
                "verbose_name": "Project Label Property",
                "verbose_name_plural": "Project Label Properties",
                "db_table": "project_label_properties",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="projectprojectlabel",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("project", "label"),
                name="project_project_label_unique_project_label_when_deleted_at_null",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="projectprojectlabel",
            unique_together={("project", "label", "deleted_at")},
        ),
    ]
