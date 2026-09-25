# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0162_affineconnection"),
    ]

    operations = [
        migrations.CreateModel(
            name="AffinePageMap",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                ("id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ("affine_doc_id", models.CharField(max_length=255)),
                ("affine_doc_title", models.CharField(blank=True, default="", max_length=512)),
                ("affine_updated_at", models.DateTimeField(blank=True, null=True)),
                ("affine_content_hash", models.CharField(blank=True, default="", max_length=64)),
                ("plane_content_hash", models.CharField(blank=True, default="", max_length=64)),
                ("plane_updated_at", models.DateTimeField(blank=True, null=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_direction", models.CharField(choices=[("pull", "AFFiNE to Plane"), ("push", "Plane to AFFiNE"), ("create", "Initial create"), ("none", "No content change")], default="none", max_length=10)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("synced", "Synced"), ("conflict", "Conflict"), ("error", "Error")], default="pending", max_length=10)),
                ("last_error", models.TextField(blank=True, default="")),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="page_maps", to="db.affineconnection")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(class)s_created_by", to=settings.AUTH_USER_MODEL, verbose_name="Created By")),
                ("page", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="affine_maps", to="db.page")),
                ("updated_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(class)s_updated_by", to=settings.AUTH_USER_MODEL, verbose_name="Last Modified By")),
            ],
            options={
                "verbose_name": "AFFiNE Page Map",
                "verbose_name_plural": "AFFiNE Page Maps",
                "db_table": "affine_page_maps",
            },
        ),
        migrations.AddIndex(
            model_name="affinepagemap",
            index=models.Index(fields=["affine_doc_id"], name="affine_pagemap_doc_idx"),
        ),
        migrations.AddConstraint(
            model_name="affinepagemap",
            constraint=models.UniqueConstraint(condition=models.Q(("deleted_at__isnull", True)), fields=("connection", "affine_doc_id"), name="affine_pagemap_unique_doc_per_connection"),
        ),
        migrations.AddConstraint(
            model_name="affinepagemap",
            constraint=models.UniqueConstraint(condition=models.Q(("deleted_at__isnull", True)), fields=("connection", "page"), name="affine_pagemap_unique_page_per_connection"),
        ),
    ]
