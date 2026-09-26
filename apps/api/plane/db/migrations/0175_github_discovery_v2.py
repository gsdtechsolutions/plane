# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0174_github_ticket_commits"),
    ]

    operations = [
        migrations.AddField(
            model_name="githubrepositorymapping",
            name="is_auto",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="githubpullrequest",
            name="remote_created_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="githubpullrequest",
            name="remote_closed_at",
            field=models.DateTimeField(null=True),
        ),
    ]
