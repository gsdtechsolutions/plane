# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Fork sidecar migration (templates worker range 0143-0144; 0143 was consumed by
# automation_execution in the base tree, so the first templates migration is 0144):
# per-project work item templates.

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
            name='IssueTemplate',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Last Modified At')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='Deleted At')),
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('description_json', models.JSONField(blank=True, default=dict)),
                ('description_html', models.TextField(blank=True, default='<p></p>')),
                ('priority', models.CharField(choices=[('urgent', 'Urgent'), ('high', 'High'), ('medium', 'Medium'), ('low', 'Low'), ('none', 'None')], default='none', max_length=30)),
                ('due_in_days', models.IntegerField(blank=True, null=True)),
                ('is_default', models.BooleanField(default=False)),
                ('assignees', models.ManyToManyField(blank=True, related_name='issue_templates', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('labels', models.ManyToManyField(blank=True, related_name='issue_templates', to='db.label')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='issue_templates', to='db.project')),
                ('state', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='issue_templates', to='db.state')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='Last Modified By')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_%(class)s', to='db.workspace')),
            ],
            options={
                'verbose_name': 'Issue Template',
                'verbose_name_plural': 'Issue Templates',
                'db_table': 'issue_templates',
                'ordering': ('-created_at',),
            },
        ),
        migrations.AddConstraint(
            model_name='issuetemplate',
            constraint=models.UniqueConstraint(condition=models.Q(('is_default', True)), fields=('project',), name='issue_template_unique_default_per_project'),
        ),
    ]
