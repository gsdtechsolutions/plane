from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("db", "0143_automation_execution")]
    operations = [
        migrations.AddField(
            model_name="intakeissue",
            name="feedback_type",
            field=models.CharField(
                max_length=16, choices=[("bug", "Bug report"), ("feature", "Feature request")], blank=True, default=""
            ),
        )
    ]
