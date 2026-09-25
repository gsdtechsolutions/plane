# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Fork sidecar migration (asana-sync lane, range 0165): Asana bidirectional sync
# sidecar tables — connections, project syncs, task/comment links, logs.

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
            name='AsanaConnection',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('pat_encrypted', models.TextField()),
                ('asana_workspace_gid', models.CharField(blank=True, default='', max_length=64)),
                ('asana_workspace_name', models.CharField(blank=True, default='', max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('last_verified_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='asana_connections', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Asana Connection',
                'verbose_name_plural': 'Asana Connections',
                'db_table': 'asana_connections',
                'ordering': ('-created_at',),
            },
        ),
        migrations.CreateModel(
            name='AsanaProjectSync',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('asana_project_gid', models.CharField(max_length=64)),
                ('asana_project_name', models.CharField(blank=True, default='', max_length=255)),
                ('direction', models.CharField(choices=[('pull', 'Pull from Asana'), ('push', 'Push to Asana'), ('bidirectional', 'Bidirectional')], default='bidirectional', max_length=20)),
                ('sync_subtasks', models.BooleanField(default=True)),
                ('sync_comments', models.BooleanField(default=True)),
                ('state_map', models.JSONField(default=dict)),
                ('default_state_id', models.UUIDField(blank=True, null=True)),
                ('label_map', models.JSONField(default=dict)),
                ('assignee_map', models.JSONField(default=dict)),
                ('webhook_configured', models.BooleanField(default=False)),
                ('webhook_gid', models.CharField(blank=True, default='', max_length=64)),
                ('webhook_secret', models.TextField(blank=True, default='')),
                ('initial_sync_done', models.BooleanField(default=False)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='project_syncs', to='db.asanaconnection')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='asana_syncs', to='db.project')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Asana Project Sync',
                'verbose_name_plural': 'Asana Project Syncs',
                'db_table': 'asana_project_syncs',
                'ordering': ('-created_at',),
            },
        ),
        migrations.CreateModel(
            name='AsanaTaskLink',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('asana_task_gid', models.CharField(max_length=64)),
                ('asana_modified_at', models.DateTimeField(blank=True, null=True)),
                ('plane_synced_at', models.DateTimeField(blank=True, null=True)),
                ('asana_name_hash', models.CharField(blank=True, default='', max_length=64)),
                ('issue', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='asana_task_links', to='db.issue')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project')),
                ('sync', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='task_links', to='db.asanaprojectsync')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Asana Task Link',
                'verbose_name_plural': 'Asana Task Links',
                'db_table': 'asana_task_links',
                'ordering': ('-created_at',),
            },
        ),
        migrations.CreateModel(
            name='AsanaCommentLink',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('asana_story_gid', models.CharField(max_length=64)),
                ('direction', models.CharField(default='pull', max_length=10)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('issue_comment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='asana_comment_links', to='db.issuecomment')),
                ('task_link', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comment_links', to='db.asanatasklink')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
            ],
            options={
                'verbose_name': 'Asana Comment Link',
                'verbose_name_plural': 'Asana Comment Links',
                'db_table': 'asana_comment_links',
                'ordering': ('created_at',),
            },
        ),
        migrations.CreateModel(
            name='AsanaSyncLog',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('direction', models.CharField(default='pull', max_length=10)),
                ('entity_type', models.CharField(max_length=20)),
                ('entity_gid', models.CharField(blank=True, default='', max_length=64)),
                ('status', models.CharField(choices=[('success', 'Success'), ('error', 'Error'), ('skipped', 'Skipped'), ('conflict', 'Conflict resolved')], default='success', max_length=16)),
                ('message', models.CharField(blank=True, default='', max_length=512)),
                ('detail', models.JSONField(blank=True, default=dict, null=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('issue', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='asana_sync_logs', to='db.issue')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='project_%(class)s', to='db.project')),
                ('sync', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='logs', to='db.asanaprojectsync')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Asana Sync Log',
                'verbose_name_plural': 'Asana Sync Logs',
                'db_table': 'asana_sync_logs',
                'ordering': ('-created_at',),
            },
        ),
        migrations.AddConstraint(
            model_name='asanaconnection',
            constraint=models.UniqueConstraint(condition=models.Q(deleted_at__isnull=True), fields=('workspace', 'name'), name='asana_connection_unique_name_workspace_when_deleted_at_null'),
        ),
        migrations.AddConstraint(
            model_name='asanaprojectsync',
            constraint=models.UniqueConstraint(condition=models.Q(deleted_at__isnull=True), fields=('project', 'asana_project_gid'), name='asana_sync_unique_project_gid_when_deleted_at_null'),
        ),
        migrations.AddConstraint(
            model_name='asanatasklink',
            constraint=models.UniqueConstraint(condition=models.Q(deleted_at__isnull=True), fields=('sync', 'issue'), name='asana_task_link_unique_issue_when_deleted_at_null'),
        ),
        migrations.AddConstraint(
            model_name='asanatasklink',
            constraint=models.UniqueConstraint(condition=models.Q(deleted_at__isnull=True), fields=('sync', 'asana_task_gid'), name='asana_task_link_unique_task_when_deleted_at_null'),
        ),
        migrations.AddConstraint(
            model_name='asanacommentlink',
            constraint=models.UniqueConstraint(condition=models.Q(deleted_at__isnull=True), fields=('task_link', 'asana_story_gid'), name='asana_comment_link_unique_story_when_deleted_at_null'),
        ),
        migrations.AddIndex(
            model_name='asanasynclog',
            index=models.Index(fields=['sync', '-created_at'], name='asana_sync_log_sync_created'),
        ),
    ]
