from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = "inventory"

    def ready(self):
        from inventory.demo import refuse_unsafe_settings

        refuse_unsafe_settings()
