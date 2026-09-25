# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0143_automation_execution"),
    ]

    operations = [
        migrations.CreateModel(
            name="AffineConnection",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                ("id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ("affine_instance_url", models.URLField(max_length=255)),
                ("affine_workspace_id", models.CharField(max_length=255)),
                ("affine_workspace_name", models.CharField(blank=True, default="", max_length=255)),
                ("api_token", models.TextField()),
                ("settings", models.JSONField(blank=True, default=dict)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_status", models.CharField(choices=[("never", "Never synced"), ("ok", "OK"), ("error", "Error")], default="never", max_length=10)),
                ("last_sync_error", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(class)s_created_by", to=settings.AUTH_USER_MODEL, verbose_name="Created By")),
                ("owned_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="affine_connections", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="affine_connections", to="db.project")),
                ("updated_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(class)s_updated_by", to=settings.AUTH_USER_MODEL, verbose_name="Last Modified By")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="affine_connections", to="db.workspace")),
            ],
            options={
                "verbose_name": "AFFiNE Connection",
                "verbose_name_plural": "AFFiNE Connections",
                "db_table": "affine_connections",
            },
        ),
        migrations.AddConstraint(
            model_name="affineconnection",
            constraint=models.UniqueConstraint(condition=models.Q(("deleted_at__isnull", True)), fields=("workspace",), name="affine_connection_unique_per_workspace"),
        ),
    ]
