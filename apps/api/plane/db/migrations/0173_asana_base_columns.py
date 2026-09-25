# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Repair: 0165 created the asana sidecar tables without the ProjectBaseModel /
# WorkspaceBaseModel columns the models carry (project, workspace), so every
# asana model query failed against the applied schema. All five tables were
# empty when this ran; the non-nullable additions below require that.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0172_github_org_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="asanacommentlink",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="project_%(class)s",
                to="db.project",
            ),
        ),
        migrations.AddField(
            model_name="asanacommentlink",
            name="workspace",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="workspace_%(class)s",
                to="db.workspace",
            ),
        ),
        migrations.AddField(
            model_name="asanaconnection",
            name="project",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="project_%(class)s",
                to="db.project",
            ),
        ),
    ]
