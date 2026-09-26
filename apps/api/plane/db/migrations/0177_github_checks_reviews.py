# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import django.db.models.deletion
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0175_github_discovery_v2"),
    ]

    operations = [
        migrations.CreateModel(
            name="GitHubPullRequestReview",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("review_id", models.PositiveBigIntegerField()),
                ("user_login", models.CharField(blank=True, max_length=100)),
                ("state", models.CharField(max_length=32)),
                ("submitted_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "pull_request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reviews",
                        to="db.githubpullrequest",
                    ),
                ),
            ],
            options={},
        ),
        migrations.CreateModel(
            name="GitHubCheckRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("check_run_id", models.PositiveBigIntegerField()),
                ("name", models.CharField(max_length=255)),
                ("conclusion", models.CharField(blank=True, max_length=32)),
                ("status", models.CharField(max_length=16)),
                ("completed_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "pull_request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="check_runs",
                        to="db.githubpullrequest",
                    ),
                ),
            ],
            options={},
        ),
        migrations.AddField(
            model_name="githubcommit",
            name="author_email",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddConstraint(
            model_name="githubpullrequestreview",
            constraint=models.UniqueConstraint(fields=("pull_request", "review_id"), name="github_pr_review"),
        ),
        migrations.AddConstraint(
            model_name="githubcheckrun",
            constraint=models.UniqueConstraint(fields=("pull_request", "check_run_id"), name="github_pr_check_run"),
        ),
    ]
