"""Simulated reachability checks for demo mode.

`simulated_probe` stands in for `monitoring.probe`: it answers from the fictional lab,
deterministically, and never touches the network. Apps the lab does not know, such as
ones added in the admin, are simply up.
"""

import random
from datetime import datetime, time, timedelta

from django.utils import timezone

from inventory.monitoring import CheckResult

from . import lab

DEFAULT = lab.DemoApp(name="", server="")


def simulated_probe(url, at, seeded_at=None):
    """The result of checking `url` at `at`.

    `seeded_at` is when the seed that writes the history runs, and None in a monitor run.
    An app the lab has down is down from `down_minutes` before the seed ran, and in
    every monitor run.
    """
    app = lab.app_for_url(url) or DEFAULT
    status = 200
    if app.down_status and (
        seeded_at is None or at >= seeded_at - timedelta(minutes=app.down_minutes)
    ):
        status = app.down_status
    for outage in app.outages:
        # Outages are days before the seed ran; a monitor run is never inside one.
        start = _local_day_start(seeded_at or at, outage.days_ago) + timedelta(
            hours=outage.hour, minutes=outage.minute
        )
        if start <= at < start + timedelta(minutes=outage.minutes):
            status = outage.status_code
    if status is None:
        return CheckResult(ok=False, status_code=None, response_ms=None, error="Timed out")
    # A steady response time per app, with a little jitter that is the same on every run.
    jitter = random.Random(f"{url} {at:%Y-%m-%dT%H:%M}")  # noqa: S311
    response_ms = jitter.randint(app.response_ms * 3 // 4, app.response_ms * 5 // 4)
    tls_expires_at = None
    if url.startswith("https:"):
        tls_expires_at = _local_day_start(at, -app.tls_days) + timedelta(hours=14)
    ok = status < 400
    return CheckResult(
        ok=ok,
        status_code=status,
        response_ms=response_ms,
        error="" if ok else f"HTTP {status}",
        tls_expires_at=tls_expires_at,
    )


def _local_day_start(at, days_ago):
    day = timezone.localdate(at) - timedelta(days=days_ago)
    return timezone.make_aware(datetime.combine(day, time()))
