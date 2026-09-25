# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0171_final_integration"),
    ]

    operations = [
        migrations.AddField(
            model_name="githubconnectnonce",
            name="organization",
            field=models.CharField(blank=True, default="", max_length=39),
        ),
    ]
