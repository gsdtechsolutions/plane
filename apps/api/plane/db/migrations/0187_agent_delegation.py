# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("db", "0184_ai_action_audit")]

    operations = [
        migrations.CreateModel(
            name="DelegationRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("claimed", "Claimed"),
                            ("running", "Running"),
                            ("pr_opened", "PR Opened"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("instructions", models.TextField(blank=True)),
                ("branch", models.CharField(blank=True, max_length=255)),
                ("pr_url", models.URLField(blank=True)),
                ("pr_number", models.PositiveIntegerField(blank=True, null=True)),
                ("result_excerpt", models.TextField(blank=True)),
                ("error", models.TextField(blank=True)),
                ("runner_id", models.CharField(blank=True, max_length=128)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_delegation_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="delegation_runs", to="db.issue"
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_delegation_runs",
                        to="db.project",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_delegation_runs",
                        to="db.workspace",
                    ),
                ),
            ],
            options={"ordering": ("-created_at",), "verbose_name": "Agent Delegation Run"},
        ),
    ]
