# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only

from django.db import migrations


def create_presence_schedule(apps, schema_editor):
    """Beat entry: the bot joins public channels periodically so unfurls work workspace-wide."""
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
        CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    except LookupError:
        return
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="*/30",
        hour="*",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        defaults={"timezone": "UTC"},
    )
    PeriodicTask.objects.update_or_create(
        name="slack-channel-presence",
        defaults={
            "task": "slack_delivery.presence",
            "crontab": schedule,
            "enabled": True,
        },
    )


def remove_presence_schedule(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return
    PeriodicTask.objects.filter(name="slack-channel-presence").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0182_slack_notify_toggles"),
    ]

    operations = [
        migrations.RunPython(create_presence_schedule, remove_presence_schedule),
    ]
