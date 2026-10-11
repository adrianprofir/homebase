"""Alerts: what needs attention now, and notify about them when a channel is configured.

`current_alerts()` works the alerts out from the inventory as it is right now and never
writes; the dashboard shows exactly that list. `update_alerts()`, run by `monitor`,
stores them as Alert rows, resolves the ones that cleared, and notifies each alert once
when it is raised, again if it turns critical, and once more when it resolves.
Delivery uses SMTP, an ntfy topic, and/or a generic webhook when configured.
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import Alert, App, Domain, Provider, Subscription
from .providers import INTEGRATIONS

logger = logging.getLogger("homebase.alerts")

CRITICAL_DAYS = 7
TLS_CRITICAL_DAYS = 3
RESOLVED_ALERT_RETENTION_DAYS = 90
NTFY_MAX_MESSAGE_BYTES = 4096
RANK = {"": 0, Alert.Severity.WARNING: 1, Alert.Severity.CRITICAL: 2}
CHANNELS = (
    ("email", "HOMEBASE_ALERT_EMAIL_ENABLED", "emailed_severity", "resolution_emailed"),
    ("ntfy", "HOMEBASE_ALERT_NTFY_ENABLED", "ntfy_emailed_severity", "ntfy_resolution_emailed"),
    (
        "webhook",
        "HOMEBASE_ALERT_WEBHOOK_ENABLED",
        "webhook_emailed_severity",
        "webhook_resolution_emailed",
    ),
)


@dataclass(frozen=True)
class AlertSpec:
    key: str
    kind: str
    severity: str
    title: str
    link: str

    @property
    def critical(self):
        return self.severity == Alert.Severity.CRITICAL


def current_alerts(now):
    """Every alert that applies at `now`, most severe first."""
    today = timezone.localdate(now)
    alerts = [
        *_domain_alerts(today),
        *_subscription_alerts(today),
        *_app_alerts(now),
        *_sync_alerts(),
    ]
    return sorted(alerts, key=lambda a: (-RANK[a.severity], a.kind, a.title))


def _expiry_severity(days_left, auto_renew, warn_on_auto_renew):
    """Manual renewals warn ahead of time; auto-renewals only when they should have run."""
    if not auto_renew:
        if days_left <= CRITICAL_DAYS:
            return Alert.Severity.CRITICAL
        if days_left <= settings.HOMEBASE_EXPIRY_ALERT_DAYS:
            return Alert.Severity.WARNING
        return None
    if days_left < 0:
        return Alert.Severity.CRITICAL if warn_on_auto_renew else Alert.Severity.WARNING
    if warn_on_auto_renew and days_left <= CRITICAL_DAYS:
        return Alert.Severity.WARNING
    return None


def _when(days_left, verb_future, verb_past):
    if days_left == 0:
        return f"{verb_future} today"
    days = abs(days_left)
    unit = "day" if days == 1 else "days"
    if days_left > 0:
        return f"{verb_future} in {days} {unit}"
    return f"{verb_past} {days} {unit} ago"


def _domain_alerts(today):
    horizon = today + timedelta(days=max(settings.HOMEBASE_EXPIRY_ALERT_DAYS, CRITICAL_DAYS))
    for domain in Domain.objects.filter(expires_on__lte=horizon):
        days_left = (domain.expires_on - today).days
        # A lapsed domain takes its sites and mail down, so auto-renew still gets a warning.
        severity = _expiry_severity(days_left, domain.auto_renew, warn_on_auto_renew=True)
        if severity is None:
            continue
        if domain.auto_renew:
            note = "auto-renew is on, check that it went through"
        else:
            note = "auto-renew is off"
        yield AlertSpec(
            key=f"domain_expiry:{domain.pk}",
            kind=Alert.Kind.DOMAIN_EXPIRY,
            severity=severity,
            title=f"{domain.name} {_when(days_left, 'expires', 'expired')} "
            f"({domain.expires_on:%Y-%m-%d}), {note}",
            link=reverse("admin:inventory_domain_change", args=[domain.pk]),
        )


def _subscription_alerts(today):
    horizon = today + timedelta(days=max(settings.HOMEBASE_EXPIRY_ALERT_DAYS, CRITICAL_DAYS))
    # A domain registration renews when its domain expires, and the domain alert covers that.
    subscriptions = Subscription.objects.filter(
        active=True, next_renewal__lte=horizon, domain__isnull=True
    ).select_related("provider")
    for sub in subscriptions:
        days_left = (sub.next_renewal - today).days
        severity = _expiry_severity(days_left, sub.auto_renew, warn_on_auto_renew=False)
        if severity is None:
            continue
        if sub.auto_renew:
            title = (
                f"{sub.name} ({sub.provider}) renewal date passed {-days_left} "
                f"{'day' if days_left == -1 else 'days'} ago; update it once renewed"
            )
        else:
            title = (
                f"{sub.name} ({sub.provider}) {_when(days_left, 'renews', 'was due')} "
                f"({sub.next_renewal:%Y-%m-%d}), auto-renew is off"
            )
        yield AlertSpec(
            key=f"subscription_renewal:{sub.pk}",
            kind=Alert.Kind.SUBSCRIPTION_RENEWAL,
            severity=severity,
            title=title,
            link=reverse("admin:inventory_subscription_change", args=[sub.pk]),
        )


def _app_alerts(now):
    """TLS expiry and sustained downtime, for live apps only."""
    down_cutoff = now - timedelta(minutes=settings.HOMEBASE_DOWNTIME_ALERT_MINUTES)
    tls_horizon = now + timedelta(days=settings.HOMEBASE_TLS_ALERT_DAYS)
    apps = App.objects.filter(status=App.Status.LIVE).exclude(url="")
    apps = apps.filter(
        Q(down_since__lte=down_cutoff)
        | Q(url__startswith="https:", last_check_tls_expires_at__lte=tls_horizon)
    )
    for app in apps:
        link = reverse("app_detail", args=[app.pk])
        if app.down_since is not None and app.down_since <= down_cutoff:
            reason = app.last_check_error or "check failed"
            yield AlertSpec(
                key=f"downtime:{app.pk}",
                kind=Alert.Kind.DOWNTIME,
                severity=Alert.Severity.CRITICAL,
                title=f"{app.name} has been down for {duration(now - app.down_since)} ({reason})",
                link=link,
            )
        expires_at = app.last_check_tls_expires_at
        if app.url.startswith("https:") and expires_at is not None and expires_at <= tls_horizon:
            days_left = math.floor((expires_at - now).total_seconds() / 86400)
            if expires_at <= now:
                when = "has expired"
            elif days_left == 0:
                when = "expires within a day"
            else:
                when = _when(days_left, "expires", "expired")
            yield AlertSpec(
                key=f"tls_expiry:{app.pk}",
                kind=Alert.Kind.TLS_EXPIRY,
                severity=(
                    Alert.Severity.CRITICAL
                    if days_left <= TLS_CRITICAL_DAYS
                    else Alert.Severity.WARNING
                ),
                title=f"TLS certificate of {app.name} {when} "
                f"({timezone.localtime(expires_at):%Y-%m-%d})",
                link=link,
            )


def _sync_alerts():
    configured = [key for key, integration in INTEGRATIONS.items() if integration.configured]
    for provider in Provider.objects.filter(integration__in=configured).exclude(sync_error=""):
        yield AlertSpec(
            key=f"sync_failure:{provider.integration}",
            kind=Alert.Kind.SYNC_FAILURE,
            severity=Alert.Severity.WARNING,
            title=f"{provider.name} sync is failing: {provider.sync_error}"[:255],
            link=reverse("dashboard") + "#providers",
        )


def duration(delta):
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{hours // 24} days"


@dataclass(frozen=True)
class AlertUpdate:
    open: int
    raised: int
    resolved: int
    email: str


def update_alerts(now=None):
    """Store the current alerts, resolve cleared ones, and send email if configured."""
    now = now or timezone.now()
    current = {spec.key: spec for spec in current_alerts(now)}
    open_alerts = {alert.key: alert for alert in Alert.objects.filter(resolved_at=None)}
    raised = 0
    for key, spec in current.items():
        alert = open_alerts.get(key)
        if alert is None:
            Alert.objects.create(
                key=key,
                kind=spec.kind,
                severity=spec.severity,
                title=spec.title[:255],
                link=spec.link,
                raised_at=now,
            )
            raised += 1
            logger.info("alert raised: %s", spec.title)
        elif (alert.severity, alert.title, alert.link) != (spec.severity, spec.title, spec.link):
            alert.severity = spec.severity
            alert.title = spec.title[:255]
            alert.link = spec.link
            alert.save(update_fields=["severity", "title", "link"])
    cleared = [alert for key, alert in open_alerts.items() if key not in current]
    for alert in cleared:
        alert.resolved_at = now
        if not _any_channel_notified(alert):
            alert.email_error = ""
        alert.save(update_fields=["resolved_at", "email_error"])
        logger.info("alert resolved: %s", alert.title)
    Alert.objects.filter(
        resolved_at__lt=now - timedelta(days=RESOLVED_ALERT_RETENTION_DAYS)
    ).delete()
    email = send_alert_email(now) if alert_delivery_enabled() else "off"
    return AlertUpdate(open=len(current), raised=raised, resolved=len(cleared), email=email)


def alert_delivery_enabled():
    return (
        settings.HOMEBASE_ALERT_EMAIL_ENABLED
        or settings.HOMEBASE_ALERT_NTFY_ENABLED
        or settings.HOMEBASE_ALERT_WEBHOOK_ENABLED
    )


def pending_email_alerts():
    """Alerts still waiting on at least one configured delivery channel."""
    combined = Alert.objects.none()
    for _name, enabled_setting, severity_field, resolution_field in CHANNELS:
        if getattr(settings, enabled_setting):
            combined = combined | _pending_for_channel(severity_field, resolution_field)
    return combined.distinct()


def _pending_for_channel(severity_field, resolution_field):
    escalated = Q()
    for severity, rank in RANK.items():
        if rank:
            escalated |= Q(
                severity=severity,
                **{
                    f"{severity_field}__in": [
                        sent for sent, sent_rank in RANK.items() if sent_rank < rank
                    ]
                },
            )
    return Alert.objects.filter(
        (Q(resolved_at=None) & escalated)
        | (
            Q(resolved_at__isnull=False, **{resolution_field: False})
            & ~Q(**{severity_field: ""})
        )
    )


def _any_channel_notified(alert):
    for _name, enabled_setting, severity_field, _resolution_field in CHANNELS:
        if getattr(settings, enabled_setting) and getattr(alert, severity_field):
            return True
    return False


def _channel_pending(alert, severity_field, resolution_field):
    if alert.resolved_at is None:
        sent_rank = RANK.get(getattr(alert, severity_field), 0)
        return RANK[alert.severity] > sent_rank
    if not getattr(alert, severity_field):
        return False
    return not getattr(alert, resolution_field)


def _alert_fully_delivered(alert):
    for _name, enabled_setting, severity_field, resolution_field in CHANNELS:
        if not getattr(settings, enabled_setting):
            continue
        if _channel_pending(alert, severity_field, resolution_field):
            return False
    return True


def send_alert_email(now):
    """Notify new, escalated, and resolved alerts through every configured channel.

    Returns "sent", "nothing to send", "off", or the error. Each channel tracks its own
    delivery state so one failing channel does not re-send through the others.
    """
    if not alert_delivery_enabled():
        return "off"
    errors = []
    any_work = False
    any_sent = False
    for name, enabled_setting, severity_field, resolution_field in CHANNELS:
        if not getattr(settings, enabled_setting):
            continue
        pending = _pending_for_channel(severity_field, resolution_field)
        to_raise = list(pending.filter(resolved_at=None))
        to_resolve = list(pending.filter(resolved_at__isnull=False))
        if not to_raise and not to_resolve:
            continue
        any_work = True
        subject = _subject(to_raise, to_resolve)
        body = _body(to_raise, to_resolve)
        involved = to_raise + to_resolve
        try:
            if name == "email":
                EmailMessage(
                    subject=subject,
                    body=body,
                    to=settings.HOMEBASE_ALERT_EMAIL_TO,
                ).send()
            elif name == "ntfy":
                _send_ntfy(subject, body)
            else:
                _send_webhook(subject, body, to_raise, to_resolve)
        except Exception as exc:
            errors.append(f"{name} {type(exc).__name__}: {exc}")
            stamp = f"{timezone.localtime(now):%Y-%m-%d %H:%M}"
            for alert in involved:
                alert.email_error = f"{stamp}: {errors[-1]}"[:255]
                alert.save(update_fields=["email_error"])
            continue
        _mark_channel_delivered(to_raise, to_resolve, severity_field, resolution_field)
        any_sent = True
        if name == "email":
            logger.info(
                "alert email sent to %s: %d raised, %d resolved",
                ", ".join(settings.HOMEBASE_ALERT_EMAIL_TO),
                len(to_raise),
                len(to_resolve),
            )
        elif name == "ntfy":
            logger.info("alert ntfy sent: %d raised, %d resolved", len(to_raise), len(to_resolve))
        else:
            logger.info(
                "alert webhook sent: %d raised, %d resolved", len(to_raise), len(to_resolve)
            )
    if not any_work:
        return "nothing to send"
    if errors:
        summary = "; ".join(errors)[:255]
        logger.warning("alert delivery failed: %s", summary)
        return f"failed: {summary}"
    return "sent" if any_sent else "nothing to send"


def _mark_channel_delivered(to_raise, to_resolve, severity_field, resolution_field):
    for alert in to_raise:
        setattr(alert, severity_field, alert.severity)
        if _alert_fully_delivered(alert):
            alert.email_error = ""
        alert.save(update_fields=[severity_field, "email_error"])
    for alert in to_resolve:
        setattr(alert, resolution_field, True)
        if _alert_fully_delivered(alert):
            alert.email_error = ""
        alert.save(update_fields=[resolution_field, "email_error"])


def _send_ntfy(subject, body):
    base = settings.HOMEBASE_ALERT_NTFY_SERVER.rstrip("/")
    message = body
    while len(message.encode("utf-8")) > NTFY_MAX_MESSAGE_BYTES:
        message = message[:-1]
    if len(message.encode("utf-8")) > NTFY_MAX_MESSAGE_BYTES - 3:
        message = message[:-3] + "..."
    payload = json.dumps(
        {
            "topic": settings.HOMEBASE_ALERT_NTFY_TOPIC.strip("/"),
            "title": subject[:200],
            "message": message,
        }
    ).encode()
    request = Request(  # noqa: S310
        base,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        if response.status >= 400:
            raise HTTPError(base, response.status, response.reason, response.headers, None)


def _send_webhook(subject, body, to_raise, to_resolve):
    payload = {
        "subject": subject,
        "body": body,
        "raised": [_webhook_alert(alert) for alert in to_raise],
        "resolved": [_webhook_alert(alert) for alert in to_resolve],
    }
    data = json.dumps(payload).encode()
    request = Request(  # noqa: S310
        settings.HOMEBASE_ALERT_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        if response.status >= 400:
            raise HTTPError(
                settings.HOMEBASE_ALERT_WEBHOOK_URL,
                response.status,
                response.reason,
                response.headers,
                None,
            )


def _webhook_alert(alert):
    link = alert.link
    base = settings.HOMEBASE_DASHBOARD_URL
    if base and link:
        link = urljoin(base, link)
    return {
        "title": alert.title,
        "severity": alert.severity,
        "kind": alert.kind,
        "link": link,
    }


def _subject(to_raise, to_resolve):
    if len(to_raise) + len(to_resolve) == 1:
        alert = (to_raise or to_resolve)[0]
        prefix = "Resolved: " if to_resolve else ""
        return f"[homebase] {prefix}{alert.title}"[:200]
    parts = []
    if to_raise:
        parts.append(f"{len(to_raise)} new {'alert' if len(to_raise) == 1 else 'alerts'}")
    if to_resolve:
        parts.append(f"{len(to_resolve)} resolved")
    return f"[homebase] {', '.join(parts)}"


def _body(to_raise, to_resolve):
    base = settings.HOMEBASE_DASHBOARD_URL
    lines = []
    if to_raise:
        lines.append("Needs attention:")
        for alert in sorted(to_raise, key=lambda a: -RANK[a.severity]):
            lines.append(f"  {alert.get_severity_display().upper():<8}  {alert.title}")
            if base and alert.link:
                lines.append(f"            {urljoin(base, alert.link)}")
        lines.append("")
    if to_resolve:
        lines.append("Resolved:")
        lines += [f"  {alert.title}" for alert in to_resolve]
        lines.append("")
    if base:
        lines.append(f"Dashboard: {base}")
    return "\n".join(lines) + "\n"
