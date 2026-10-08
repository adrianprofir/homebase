from django.core.management.base import BaseCommand, CommandError

from inventory.providers import INTEGRATIONS, sync


class Command(BaseCommand):
    help = (
        "Read live data from every provider API whose token is set and store it in the "
        "inventory. Only reads from the providers; never changes anything there."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            action="append",
            choices=sorted(INTEGRATIONS),
            help="Sync only this provider. Repeat for several. Default: all configured.",
        )

    def handle(self, *args, **options):
        keys = options["provider"] or list(INTEGRATIONS)
        failed = 0
        for key in keys:
            integration = INTEGRATIONS[key]
            if not integration.configured:
                self.stdout.write(
                    f"skip  {integration.label} ({integration.token_setting} is not set)"
                )
                continue
            provider = sync(integration)
            if provider.sync_error:
                failed += 1
                self.stdout.write(
                    self.style.WARNING(f"fail  {integration.label}: {provider.sync_error}")
                )
            else:
                self.stdout.write(f"ok    {integration.label}: {provider.sync_summary}")
        if failed:
            raise CommandError(f"{failed} provider sync(s) failed")
