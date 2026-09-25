# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import django.db.models.deletion
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0173_asana_base_columns"),
    ]

    operations = [
        migrations.CreateModel(
            name="GitHubCommit",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sha", models.CharField(max_length=64)),
                ("message", models.TextField(blank=True)),
                ("author_name", models.CharField(blank=True, max_length=255)),
                ("author_login", models.CharField(blank=True, max_length=100)),
                ("committed_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "mapping",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="db.githubrepositorymapping",
                    ),
                ),
            ],
            options={},
        ),
        migrations.CreateModel(
            name="GitHubCommitIssueLink",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "commit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="db.githubcommit",
                    ),
                ),
                ("issue", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="db.issue")),
            ],
            options={},
        ),
        migrations.CreateModel(
            name="GitHubMentionSearch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("started_at", models.DateTimeField(null=True)),
                ("completed_at", models.DateTimeField(null=True)),
                ("error", models.CharField(blank=True, max_length=200)),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="db.issue",
                        unique=True,
                    ),
                ),
            ],
            options={},
        ),
        migrations.AddConstraint(
            model_name="githubcommit",
            constraint=models.UniqueConstraint(fields=("mapping", "sha"), name="github_mapping_commit_sha"),
        ),
        migrations.AddConstraint(
            model_name="githubcommitissuelink",
            constraint=models.UniqueConstraint(fields=("issue", "commit"), name="github_issue_commit_link"),
        ),
    ]
