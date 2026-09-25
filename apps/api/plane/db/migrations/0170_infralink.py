# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Fork sidecar migration (board-infra lane, reserved number 0170):
# workspace infra connections + per-project infra links (Coolify / Grafana).

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0143_automation_execution'),
    ]

    operations = [
        migrations.CreateModel(
            name='InfraConnection',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('service', models.CharField(choices=[('coolify', 'Coolify'), ('grafana', 'Grafana')], max_length=20)),
                ('base_url', models.URLField(max_length=2048)),
                ('api_token_encrypted', models.TextField(blank=True, default='')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('project', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='infra_connections', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Infra Connection',
                'verbose_name_plural': 'Infra Connections',
                'db_table': 'infra_connections',
                'ordering': ('-created_at',),
            },
        ),
        migrations.CreateModel(
            name='ProjectInfraLink',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('kind', models.CharField(choices=[('coolify_app', 'Coolify application'), ('grafana_dashboard', 'Grafana dashboard')], max_length=32)),
                ('external_id', models.CharField(blank=True, default='', max_length=255)),
                ('display_name', models.CharField(blank=True, default='', max_length=255)),
                ('external_url', models.URLField(blank=True, default='', max_length=2048)),
                ('meta', models.JSONField(blank=True, default=dict)),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='project_links', to='db.infraconnection')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='infra_links', to='db.project')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Project Infra Link',
                'verbose_name_plural': 'Project Infra Links',
                'db_table': 'project_infra_links',
                'ordering': ('-created_at',),
            },
        ),
        migrations.AddConstraint(
            model_name='infraconnection',
            constraint=models.UniqueConstraint(condition=models.Q(deleted_at__isnull=True), fields=('workspace', 'name'), name='infra_connection_unique_name_per_workspace'),
        ),
        migrations.AddConstraint(
            model_name='projectinfralink',
            constraint=models.UniqueConstraint(condition=~models.Q(external_id=''), fields=('project', 'connection', 'external_id'), name='infra_link_unique_external_id_per_project'),
        ),
    ]
