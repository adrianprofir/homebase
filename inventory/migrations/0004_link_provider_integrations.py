import re

from django.db import migrations

INTEGRATIONS = ("cloudflare", "godaddy", "digitalocean", "hostinger")


def link_integrations(apps, schema_editor):
    """Point providers entered by hand, such as "Digital Ocean", at their API integration."""
    Provider = apps.get_model("inventory", "Provider")
    taken = set(
        Provider.objects.exclude(integration="").values_list("integration", flat=True)
    )
    for provider in Provider.objects.filter(integration="").order_by("pk"):
        key = re.sub(r"[^a-z0-9]", "", provider.name.lower())
        if key in INTEGRATIONS and key not in taken:
            provider.integration = key
            provider.save(update_fields=["integration"])
            taken.add(key)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_live_data"),
    ]

    operations = [
        migrations.RunPython(link_integrations, migrations.RunPython.noop),
    ]
