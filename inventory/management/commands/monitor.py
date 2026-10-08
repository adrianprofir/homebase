import logging

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from inventory.alerts import update_alerts
from inventory.monitoring import prune_history
from inventory.providers import due_integrations, sync

logger = logging.getLogger("homebase.monitor")


class Command(BaseCommand):
    help = (
        "One monitoring run, as Docker Compose schedules it: sync the providers that are "
        "due, check every app, drop old uptime history, then update alerts and send email."
    )

    def handle(self, *args, **options):
        started = timezone.now()
        self.log(f"monitor run started at {timezone.localtime(started):%Y-%m-%d %H:%M:%S}")
        crashed = []
        # Each step runs even when an earlier one crashed, so one broken provider or a
        # bad check never stops alerts from going out.
        for name, step in [
            ("provider sync", self.sync_providers),
            ("app checks", self.check_apps),
            ("history cleanup", self.prune),
            ("alerts", self.alerts),
        ]:
            try:
                step()
            except Exception:
                logger.exception("monitor step %r crashed", name)
                crashed.append(name)
        seconds = (timezone.now() - started).total_seconds()
        self.log(f"monitor run finished in {seconds:.1f} s")
        if crashed:
            raise CommandError(f"monitor steps crashed: {', '.join(crashed)}")

    def log(self, message):
        self.stdout.write(message)

    def sync_providers(self):
        for integration in due_integrations(timezone.now()):
            provider = sync(integration)
            if provider.sync_error:
                self.log(
                    self.style.WARNING(f"sync  {integration.label} failed: {provider.sync_error}")
                )
            else:
                self.log(f"sync  {integration.label}: {provider.sync_summary}")

    def check_apps(self):
        call_command("check_apps", stdout=self.stdout, stderr=self.stderr)

    def prune(self):
        deleted = prune_history(timezone.now(), settings.HOMEBASE_CHECK_HISTORY_DAYS)
        if deleted:
            self.log(
                f"dropped {deleted} check(s) older than {settings.HOMEBASE_CHECK_HISTORY_DAYS} days"
            )

    def alerts(self):
        # Email failures are logged and shown on the dashboard by update_alerts itself.
        result = update_alerts()
        self.log(
            f"alerts: {result.open} open, {result.raised} raised, {result.resolved} resolved; "
            f"email {result.email}"
        )
