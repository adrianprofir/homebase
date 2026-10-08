"""Hostinger: VPS state, and every billing subscription with its price and expiry.

Hostinger API tokens cannot be limited to reading; homebase only sends GET requests.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from inventory.models import Server, Subscription

from .base import (
    MISSING,
    ProviderError,
    get_json,
    match_server,
    plural,
    sync_plan,
    unused_server_name,
    utc_date,
)

# Subscription statuses that still bill or run. "not_renewing" runs until it expires.
ACTIVE_STATUSES = {"active", "in_trial", "not_renewing"}
MONTHS_PER_UNIT = {"month": 1, "year": 12}


@dataclass(frozen=True)
class VirtualMachine:
    id: str
    hostname: str
    state: str
    plan: str
    ipv4: str
    subscription_id: str


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    status: str
    months: int | None
    currency: str
    renewal_price: Decimal | None
    auto_renew: bool
    next_renewal: date | None

    @property
    def active(self):
        return self.status in ACTIVE_STATUSES


@dataclass(frozen=True)
class Snapshot:
    machines: list[VirtualMachine]
    plans: list[Plan]


def fetch(http):
    machines = get_json(http, "vps/v1/virtual-machines")
    plans = get_json(http, "billing/v1/subscriptions")
    if not isinstance(machines, list) or not isinstance(plans, list):
        raise ProviderError("Hostinger did not return lists of machines and subscriptions")
    return Snapshot(machines=[_machine(m) for m in machines], plans=[_plan(p) for p in plans])


def _machine(item):
    addresses = [ip.get("address") for ip in item.get("ipv4") or [] if ip.get("address")]
    return VirtualMachine(
        id=str(item["id"]),
        hostname=item.get("hostname") or str(item["id"]),
        state=item.get("state") or "",
        plan=item.get("plan") or "",
        ipv4=addresses[0] if addresses else "",
        subscription_id=item.get("subscription_id") or "",
    )


def _plan(item):
    period = item.get("billing_period")
    per_unit = MONTHS_PER_UNIT.get(item.get("billing_period_unit"))
    price = item.get("renewal_price")
    if price is None:
        price = item.get("total_price")
    status = item.get("status") or ""
    auto_renew = bool(item.get("is_auto_renewed")) and status != "not_renewing"
    expires = utc_date(item.get("expires_at"))
    return Plan(
        id=str(item["id"]),
        name=item.get("name") or str(item["id"]),
        status=status,
        months=period * per_unit if isinstance(period, int) and per_unit else None,
        currency=(item.get("currency_code") or "").upper(),
        renewal_price=Decimal(str(price)) / 100 if price is not None else None,
        auto_renew=auto_renew,
        next_renewal=(utc_date(item.get("next_billing_at")) or expires) if auto_renew else expires,
    )


def apply(provider, snapshot, now):
    servers = {}
    added = 0
    for machine in snapshot.machines:
        server = match_server(
            provider, machine.id, machine.hostname, [machine.ipv4, machine.hostname]
        )
        if server is None:
            server = Server(
                name=unused_server_name(machine.hostname, provider),
                provider=provider,
                kind=Server.Kind.VPS,
            )
            added += 1
        server.external_id = machine.id
        server.live_status = machine.state
        server.host = server.host or machine.ipv4 or machine.hostname
        server.save()
        if machine.subscription_id:
            servers[machine.subscription_id] = server

    skipped = []
    subscriptions_before = Subscription.objects.filter(provider=provider).count()
    for plan in snapshot.plans:
        values = {"active": plan.active, "auto_renew": plan.auto_renew}
        if plan.next_renewal is not None:
            values["next_renewal"] = plan.next_renewal
        cycle = plan.months if plan.months in Subscription.BillingCycle.values else None
        if cycle and plan.renewal_price is not None and plan.currency:
            values |= {
                "billing_cycle": cycle,
                "cost": plan.renewal_price,
                "currency": plan.currency,
            }
        exists = Subscription.objects.filter(provider=provider, external_id=plan.id).exists()
        if not exists and not ({"next_renewal", "cost"} <= values.keys()):
            # Not enough to describe a new subscription; it would only be guesswork.
            if plan.active:
                skipped.append(plan.name)
            continue
        sync_plan(provider, plan.id, server=servers.get(plan.id), name=plan.name, values=values)

    known = [plan.id for plan in snapshot.plans]
    Subscription.objects.filter(provider=provider).exclude(external_id="").exclude(
        external_id__in=known
    ).update(active=False)
    gone = Server.objects.filter(provider=provider).exclude(external_id="")
    gone = gone.exclude(external_id__in=[machine.id for machine in snapshot.machines])
    missing = gone.count()
    gone.update(live_status=MISSING)

    active = sum(1 for plan in snapshot.plans if plan.active)
    new_plans = Subscription.objects.filter(provider=provider).count() - subscriptions_before
    summary = (
        f"{plural(len(snapshot.machines), 'VPS', 'VPSes')}, {plural(active, 'active subscription')}"
    )
    if added or new_plans:
        summary += f" ({plural(added, 'server')} and {plural(new_plans, 'subscription')} added)"
    if missing:
        summary += f"; {plural(missing, 'server')} no longer at Hostinger"
    if skipped:
        summary += f"; no price or renewal date for: {', '.join(skipped)}"
    return summary
