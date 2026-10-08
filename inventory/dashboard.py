"""Read-side helpers that turn the inventory into dashboard figures."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Max
from django.urls import reverse

from .models import App, AppCheck, Domain, Provider, Subscription
from .providers import INTEGRATIONS

CENT = Decimal("0.01")
SOON_DAYS = 14


@dataclass(frozen=True)
class Renewal:
    kind: str
    name: str
    provider: str
    due: date
    days_left: int
    auto_renew: bool
    amount: str
    admin_url: str

    @property
    def overdue(self):
        return self.days_left < 0

    @property
    def soon(self):
        return 0 <= self.days_left <= SOON_DAYS

    @property
    def due_label(self):
        if self.days_left == 0:
            return "Due today"
        days = abs(self.days_left)
        unit = "day" if days == 1 else "days"
        return f"{days} {unit} overdue" if self.overdue else f"In {days} {unit}"


@dataclass(frozen=True)
class CostTotal:
    currency: str
    subscriptions: int
    monthly: Decimal
    yearly: Decimal


def upcoming_renewals(today, window_days):
    """Domain expiries and subscription renewals due within the window, overdue ones included."""
    horizon = today + timedelta(days=window_days)
    renewals = [
        Renewal(
            kind="Domain",
            name=domain.name,
            provider=domain.registrar.name,
            due=domain.expires_on,
            days_left=(domain.expires_on - today).days,
            auto_renew=domain.auto_renew,
            amount="",
            admin_url=reverse("admin:inventory_domain_change", args=[domain.pk]),
        )
        for domain in Domain.objects.filter(expires_on__lte=horizon).select_related("registrar")
    ]
    renewals += [
        Renewal(
            kind="Subscription",
            name=sub.name,
            provider=sub.provider.name,
            due=sub.next_renewal,
            days_left=(sub.next_renewal - today).days,
            auto_renew=sub.auto_renew,
            amount=f"{sub.cost} {sub.currency}",
            admin_url=reverse("admin:inventory_subscription_change", args=[sub.pk]),
        )
        for sub in Subscription.objects.filter(
            active=True, next_renewal__lte=horizon
        ).select_related("provider")
    ]
    return sorted(renewals, key=lambda r: (r.due, r.kind, r.name))


def cost_totals():
    """Monthly and yearly spend on active subscriptions, one row per currency."""
    totals = {}
    for sub in Subscription.objects.filter(active=True):
        count, monthly, yearly = totals.get(sub.currency, (0, Decimal(0), Decimal(0)))
        totals[sub.currency] = (count + 1, monthly + sub.monthly_cost, yearly + sub.yearly_cost)
    return [
        CostTotal(
            currency=currency,
            subscriptions=count,
            monthly=monthly.quantize(CENT),
            yearly=yearly.quantize(CENT),
        )
        for currency, (count, monthly, yearly) in sorted(totals.items())
    ]


def server_costs():
    """{server_id: ["6.00 USD", ...]} per month, from the active subscriptions paying for it."""
    totals = {}
    for sub in Subscription.objects.filter(active=True, server__isnull=False):
        per_currency = totals.setdefault(sub.server_id, {})
        per_currency[sub.currency] = per_currency.get(sub.currency, Decimal(0)) + sub.monthly_cost
    return {
        server_id: [f"{amount.quantize(CENT)} {currency}" for currency, amount in sorted(c.items())]
        for server_id, c in totals.items()
    }


@dataclass(frozen=True)
class ProviderStatus:
    label: str
    token_setting: str
    configured: bool
    provider: Provider | None
    state: str

    @property
    def badge(self):
        return {
            "ok": ("badge-ok", "Synced"),
            "failing": ("badge-bad", "Failing"),
            "stale": ("badge-warn", "Stale"),
            "pending": ("", "Waiting for first sync"),
            "off": ("", "Not configured"),
        }[self.state]


def provider_statuses(now):
    """One row per provider integration, configured or not, for the dashboard."""
    providers = {p.integration: p for p in Provider.objects.exclude(integration="")}
    stale_before = now - timedelta(minutes=3 * settings.HOMEBASE_SYNC_INTERVAL_MINUTES)
    rows = []
    for integration in INTEGRATIONS.values():
        provider = providers.get(integration.key)
        if not integration.configured:
            state = "off"
        elif provider is None or provider.sync_attempted_at is None:
            state = "pending"
        elif provider.sync_error:
            state = "failing"
        elif provider.sync_succeeded_at < stale_before:
            state = "stale"
        else:
            state = "ok"
        rows.append(
            ProviderStatus(
                label=integration.label,
                token_setting=integration.token_setting,
                configured=integration.configured,
                provider=provider,
                state=state,
            )
        )
    return rows


@dataclass(frozen=True)
class MonitorHealth:
    last_check_at: datetime | None
    stalled: bool


def monitor_health(now):
    """Whether `monitor` still runs: a stalled monitor cannot alert about itself."""
    if not App.checkable().exists():
        return MonitorHealth(last_check_at=None, stalled=False)
    last = AppCheck.objects.aggregate(last=Max("checked_at"))["last"]
    # Three missed runs, plus slack for slow checks.
    allowed = timedelta(seconds=3 * settings.HOMEBASE_CHECK_INTERVAL_SECONDS + 60)
    return MonitorHealth(last_check_at=last, stalled=last is None or now - last > allowed)
