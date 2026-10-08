from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import App
from inventory.monitoring import check_app


class Command(BaseCommand):
    help = (
        "HTTP-check every non-retired app with a URL, read its TLS certificate expiry, and "
        "record the result and uptime history only if that URL is still current."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=float,
            default=settings.HOMEBASE_CHECK_TIMEOUT_SECONDS,
            help="Seconds to wait for each app before marking it down.",
        )

    def handle(self, *args, **options):
        apps = list(App.checkable())
        down = 0
        for app in apps:
            try:
                result = check_app(app, options["timeout"])
            except Exception as exc:
                # Keep going so one unexpected probe failure cannot skip later apps.
                down += 1
                self.stdout.write(self.style.WARNING(f"down  {app.name} ({exc})"))
            else:
                if result is None:
                    continue
                if result.ok:
                    tls = ""
                    if result.tls_expires_at is not None:
                        days = (result.tls_expires_at - timezone.now()).days
                        tls = f", TLS expires in {days} d"
                    self.stdout.write(
                        f"up    {app.name} ({result.status_code}, {result.response_ms} ms{tls})"
                    )
                else:
                    down += 1
                    self.stdout.write(self.style.WARNING(f"down  {app.name} ({result.error})"))
        self.stdout.write(f"Checked {len(apps)} app(s), {down} down.")
