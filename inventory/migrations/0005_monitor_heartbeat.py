from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0004_link_provider_integrations"),
    ]

    operations = [
        migrations.CreateModel(
            name="MonitorHeartbeat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("last_run_at", models.DateTimeField()),
            ],
            options={
                "verbose_name_plural": "monitor heartbeat",
            },
        ),
    ]
