# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Squashed port of orca-ref migrations 0122_projectcustomsettings,
# 0127_projectcustomsettings_cycle_auto_complete and
# 0129_projectcustomsettings_auto_conventional_commit_labels into the
# cycles worker migration range (0130-0139).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0122_alter_draftissue_assignees_alter_issue_assignees_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectCustomSettings',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('parallel_cycles', models.BooleanField(default=False)),
                ('cycle_auto_complete', models.BooleanField(default=False)),
                ('auto_conventional_commit_labels', models.BooleanField(default=False)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('project', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='custom_settings', to='db.project')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Project Custom Settings',
                'verbose_name_plural': 'Project Custom Settings',
                'db_table': 'project_custom_settings',
                'ordering': ('-created_at',),
            },
        ),
    ]
