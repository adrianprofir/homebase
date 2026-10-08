from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.demo import seed


class Command(BaseCommand):
    help = (
        "Fill an empty inventory with a fictional home lab: providers, domains, servers, "
        "subscriptions, and apps, a provider sync against canned API responses, and 30 days "
        "of simulated uptime history. All dates are relative to today. Makes no network "
        "request."
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--reset",
            action="store_true",
            help="Delete the whole inventory first. Only allowed with DEMO_MODE=true.",
        )
        group.add_argument(
            "--if-empty",
            action="store_true",
            help="Do nothing, successfully, when the inventory already has data.",
        )

    def handle(self, *args, **options):
        if options["reset"] and not settings.DEMO_MODE:
            raise CommandError(
                "--reset deletes the whole inventory, so it only runs with DEMO_MODE=true."
            )
        # One transaction, so a failed seed leaves the previous inventory in place.
        with transaction.atomic():
            self.build(reset=options["reset"], if_empty=options["if_empty"])

    def build(self, reset, if_empty):
        if reset:
            seed.reset()
        elif not seed.inventory_is_empty():
            if if_empty:
                self.stdout.write("The inventory already has data; not seeding.")
                return
            raise CommandError(
                "The inventory is not empty. seed_demo only fills an empty one; in demo "
                "mode, --reset rebuilds it."
            )
        try:
            summary = seed.seed()
        except seed.SeedError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(summary))
