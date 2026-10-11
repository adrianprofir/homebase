from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = "inventory"

    def ready(self):
        from homebase.secrets import refuse_placeholder_secrets
        from inventory.demo import refuse_unsafe_settings

        refuse_placeholder_secrets()
        refuse_unsafe_settings()
