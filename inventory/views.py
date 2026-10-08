from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from . import uptime
from .alerts import current_alerts, duration, pending_email_alerts
from .dashboard import (
    cost_totals,
    monitor_health,
    provider_statuses,
    server_costs,
    upcoming_renewals,
)
from .models import Alert, App, Domain, Provider, Server, Subscription, Tunnel

RECENT_CHECKS = 24
OUTAGE_DAYS = 30


def login_required_unless_demo(view):
    """Require a login, except in demo mode, where the read-only pages are public."""
    protected = login_required(view)

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if settings.DEMO_MODE:
            return view(request, *args, **kwargs)
        return protected(request, *args, **kwargs)

    return wrapper


@login_required_unless_demo
@require_GET
def dashboard(request):
    now = timezone.now()
    today = timezone.localdate(now)
    window_days = settings.HOMEBASE_RENEWAL_WINDOW_DAYS
    renewals = upcoming_renewals(today, window_days)
    apps = list(App.objects.exclude(status=App.Status.RETIRED).select_related("server"))
    app_ids = [app.pk for app in apps]
    strips = uptime.last_day_buckets(app_ids, now)
    uptime_day = uptime.uptime_percent(app_ids, now - timedelta(days=1))
    uptime_week = uptime.uptime_percent(app_ids, now - timedelta(days=7))
    for app in apps:
        app.strip = strips[app.pk]
        app.uptime_day = uptime_day[app.pk]
        app.uptime_week = uptime_week[app.pk]
        app.down_for = duration(now - app.down_since) if app.down_since else ""
        app.tls_days_left = (
            (app.last_check_tls_expires_at - now).days if app.last_check_tls_expires_at else None
        )
    costs = server_costs()
    servers = list(
        Server.objects.select_related("provider")
        .annotate(app_count=Count("apps", filter=~Q(apps__status=App.Status.RETIRED)))
        .order_by("name")
    )
    for server in servers:
        server.monthly_costs = costs.get(server.pk, [])
    alerts = current_alerts(now)
    raised_at = dict(
        Alert.objects.filter(resolved_at=None, key__in=[a.key for a in alerts]).values_list(
            "key", "raised_at"
        )
    )
    context = {
        "now": now,
        "today": today,
        "window_days": window_days,
        "counts": {
            "providers": Provider.objects.count(),
            "domains": Domain.objects.count(),
            "subscriptions": Subscription.objects.filter(active=True).count(),
            "servers": len(servers),
            "apps": len(apps),
        },
        "apps_down": sum(1 for app in apps if app.url and app.last_check_ok is False),
        "overdue_count": sum(1 for r in renewals if r.overdue),
        "alerts": [(alert, raised_at.get(alert.key)) for alert in alerts],
        "critical_count": sum(1 for alert in alerts if alert.critical),
        "monitor": monitor_health(now),
        "email_enabled": settings.HOMEBASE_ALERT_EMAIL_ENABLED,
        "email_to": settings.HOMEBASE_ALERT_EMAIL_TO,
        "email_error": (
            pending_email_alerts()
            .exclude(email_error="")
            .values_list("email_error", flat=True)
            .first()
        ),
        "renewals": renewals,
        "cost_totals": cost_totals(),
        "domains": Domain.objects.select_related("registrar", "dns_provider").annotate(
            record_count=Count("dns_records")
        ),
        "servers": servers,
        "tunnels": Tunnel.objects.select_related("provider", "server"),
        "providers": provider_statuses(now),
        "sync_interval": settings.HOMEBASE_SYNC_INTERVAL_MINUTES,
        "apps": apps,
        "tls_alert_days": settings.HOMEBASE_TLS_ALERT_DAYS,
    }
    return render(request, "inventory/dashboard.html", context)


@login_required_unless_demo
@require_GET
def app_detail(request, pk):
    app = get_object_or_404(App.objects.select_related("server"), pk=pk)
    now = timezone.now()
    since = {days: now - timedelta(days=days) for days in (1, 7, 30)}
    uptime_by_window = {
        days: uptime.uptime_percent([app.pk], start)[app.pk] for days, start in since.items()
    }
    context = {
        "app": app,
        "now": now,
        "down_for": duration(now - app.down_since) if app.down_since else "",
        "tls_days_left": (
            (app.last_check_tls_expires_at - now).days if app.last_check_tls_expires_at else None
        ),
        "tls_alert_days": settings.HOMEBASE_TLS_ALERT_DAYS,
        "uptime": uptime_by_window,
        "average_ms": uptime.average_response_ms(app.pk, since[1]),
        "days": uptime.daily_rows(app.pk, now, days=7),
        "outages": [
            (outage, duration(outage.duration(now)))
            for outage in uptime.outages(app.pk, now - timedelta(days=OUTAGE_DAYS))
        ],
        "outage_days": OUTAGE_DAYS,
        "checks": uptime.current_checks([app.pk])[:RECENT_CHECKS],
        "history_days": settings.HOMEBASE_CHECK_HISTORY_DAYS,
    }
    return render(request, "inventory/app_detail.html", context)
