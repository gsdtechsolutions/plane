# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Fork feature: Asana sync — assignee mirror custom property.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0186_agent_delegation"),
    ]

    operations = [
        migrations.AddField(
            model_name="asanaprojectsync",
            name="assignee_property_id",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
