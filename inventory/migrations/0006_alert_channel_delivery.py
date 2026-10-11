from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0005_monitor_heartbeat"),
    ]

    operations = [
        migrations.AddField(
            model_name="alert",
            name="ntfy_emailed_severity",
            field=models.CharField(
                blank=True,
                choices=[("warning", "Warning"), ("critical", "Critical")],
                help_text="The highest severity an ntfy notification has gone out for.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="alert",
            name="ntfy_resolution_emailed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="alert",
            name="webhook_emailed_severity",
            field=models.CharField(
                blank=True,
                choices=[("warning", "Warning"), ("critical", "Critical")],
                help_text="The highest severity a webhook notification has gone out for.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="alert",
            name="webhook_resolution_emailed",
            field=models.BooleanField(default=False),
        ),
    ]
