from django.core.management.base import BaseCommand, CommandError

from inventory.alerts import update_alerts


class Command(BaseCommand):
    help = (
        "Raise and resolve alerts for expiries, renewals, TLS certificates, downtime, and "
        "provider sync failures, and email the changes when SMTP is configured."
    )

    def handle(self, *args, **options):
        result = update_alerts()
        self.stdout.write(
            f"{result.open} open alert(s): {result.raised} raised, {result.resolved} resolved; "
            f"email {result.email}"
        )
        if result.email.startswith("failed"):
            raise CommandError("alert email failed")
