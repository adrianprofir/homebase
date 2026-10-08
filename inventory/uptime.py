"""Read-side helpers that turn the uptime history into strips, percentages, and outages.

History only counts for the current URL and monitoring period.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.db.models import Avg, Count, F, Q
from django.utils import timezone

from .models import AppCheck

HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class Bucket:
    """One hour of checks."""

    start: datetime
    total: int = 0
    failed: int = 0
    future: bool = False

    @property
    def state(self):
        if self.future:
            return "future"
        if self.total == 0:
            return "none"
        if self.failed == 0:
            return "up"
        return "down" if self.failed == self.total else "partial"

    @property
    def label(self):
        start = timezone.localtime(self.start)
        end = timezone.localtime(self.start.astimezone(UTC) + HOUR)
        span = f"{start:%a %d %b %H:%M}-{end:%H:%M}"
        if self.future:
            return span
        if self.total == 0:
            return f"{span}: no checks"
        if self.failed == 0:
            return f"{span}: all {self.total} checks up"
        return f"{span}: {self.failed} of {self.total} checks failed"


@dataclass(frozen=True)
class Outage:
    started_at: datetime
    ended_at: datetime | None
    checks: int
    error: str

    def duration(self, now):
        return (self.ended_at or now) - self.started_at


def current_checks(app_ids):
    return AppCheck.objects.filter(app_id__in=app_ids, url=F("app__url")).filter(
        Q(app__monitoring_since__isnull=True) | Q(checked_at__gte=F("app__monitoring_since"))
    )


def hour_floor(moment):
    # Hours are counted in UTC so arithmetic stays exact across daylight saving changes.
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def hourly_buckets(app_ids, start, end, now):
    """Hourly buckets from `start` up to `end` for each app, as {app_id: [Bucket, ...]}."""
    start = start.astimezone(UTC)
    hours = int((end - start) / HOUR)
    counts = {app_id: [[0, 0] for _ in range(hours)] for app_id in app_ids}
    rows = current_checks(app_ids).filter(checked_at__gte=start, checked_at__lt=end)
    for app_id, checked_at, ok in rows.values_list("app_id", "checked_at", "ok"):
        index = int((checked_at - start) / HOUR)
        counts[app_id][index][0] += 1
        counts[app_id][index][1] += 0 if ok else 1
    return {
        app_id: [
            Bucket(
                start=start + i * HOUR,
                total=total,
                failed=failed,
                future=start + i * HOUR > now,
            )
            for i, (total, failed) in enumerate(per_hour)
        ]
        for app_id, per_hour in counts.items()
    }


def last_day_buckets(app_ids, now):
    """The last 24 hours for each app, the current hour last."""
    end = hour_floor(now) + HOUR
    return hourly_buckets(app_ids, end - 24 * HOUR, end, now)


def uptime_percent(app_ids, since):
    """{app_id: percentage of checks that were up since `since`}, None without checks."""
    rows = (
        current_checks(app_ids)
        .filter(checked_at__gte=since)
        .values("app_id")
        .annotate(total=Count("id"), up=Count("id", filter=Q(ok=True)))
    )
    result = dict.fromkeys(app_ids)
    for row in rows:
        result[row["app_id"]] = 100 * row["up"] / row["total"]
    return result


def average_response_ms(app_id, since):
    return (
        current_checks([app_id])
        .filter(checked_at__gte=since, ok=True)
        .aggregate(avg=Avg("response_ms"))["avg"]
    )


def outages(app_id, since):
    """Runs of consecutive failed checks since `since`, newest first."""
    rows = (
        current_checks([app_id])
        .filter(checked_at__gte=since)
        .order_by("checked_at")
        .values_list("checked_at", "ok", "error")
    )
    found = []
    current = None
    for checked_at, ok, error in rows:
        if not ok:
            if current is None:
                current = {"started_at": checked_at, "checks": 0, "error": error}
            current["checks"] += 1
        elif current is not None:
            found.append(Outage(ended_at=checked_at, **current))
            current = None
    if current is not None:
        found.append(Outage(ended_at=None, **current))
    return list(reversed(found))


def daily_rows(app_id, now, days):
    """The last `days` local calendar days of hourly buckets, oldest first, as (date, buckets)."""
    today = timezone.localdate(now)
    first = today - timedelta(days=days - 1)
    start = timezone.make_aware(datetime.combine(first, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(today + timedelta(days=1), datetime.min.time()))
    rows = {}
    for bucket in hourly_buckets([app_id], start, end, now)[app_id]:
        rows.setdefault(timezone.localtime(bucket.start).date(), []).append(bucket)
    return list(rows.items())
