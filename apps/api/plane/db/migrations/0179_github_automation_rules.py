# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import django.db.models.deletion
import uuid
from django.db import migrations, models


def carry_automation_forward(apps, schema_editor):
    """Each single-toggle GitHubProjectAutomation becomes a catch-all rule."""
    Automation = apps.get_model("db", "GitHubProjectAutomation")
    Rule = apps.get_model("db", "GitHubAutomationRule")
    for row in Automation.objects.filter(enabled=True, target_state__isnull=False):
        Rule.objects.create(
            project_id=row.project_id,
            enabled=True,
            base_branch="",
            target_state_id=row.target_state_id,
            assignee=None,
            require_all_merged=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0178_merge_20260926_0132"),
    ]

    operations = [
        migrations.AddField(
            model_name="githubpullrequest",
            name="base_ref",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.CreateModel(
            name="GitHubAutomationRule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("enabled", models.BooleanField(default=True)),
                ("base_branch", models.CharField(blank=True, default="", max_length=255)),
                ("require_all_merged", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assignee",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="github_automation_rules",
                        to="db.user",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="github_automation_rules_created",
                        to="db.user",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="github_automation_rules",
                        to="db.project",
                    ),
                ),
                (
                    "target_state",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="github_automation_rules",
                        to="db.state",
                    ),
                ),
            ],
        ),
        migrations.RunPython(carry_automation_forward, migrations.RunPython.noop),
        migrations.DeleteModel(name="GitHubProjectAutomation"),
    ]
