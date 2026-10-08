"""DigitalOcean: droplets with their status and monthly price.

Token scope: droplet:read. The price is the droplet size's list price; backups, volumes,
and bandwidth overage are billed on top.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.utils import timezone

from inventory.models import Server, Subscription

from .base import (
    MISSING,
    ProviderError,
    get_json,
    match_server,
    plural,
    sync_plan,
    unused_server_name,
)

PAGE_SIZE = 200
MAX_PAGES = 100


@dataclass(frozen=True)
class Droplet:
    id: str
    name: str
    status: str
    price_monthly: Decimal | None
    region: str
    public_ipv4: str


def fetch(http):
    droplets = []
    for page in range(1, MAX_PAGES + 1):
        body = get_json(http, "droplets", {"page": page, "per_page": PAGE_SIZE})
        if not isinstance(body, dict) or not isinstance(body.get("droplets"), list):
            raise ProviderError("droplets did not return a list of droplets")
        droplets += [_droplet(item) for item in body["droplets"]]
        if not ((body.get("links") or {}).get("pages") or {}).get("next"):
            return droplets
    raise ProviderError(f"droplets has more than {MAX_PAGES} pages")


def _droplet(item):
    price = (item.get("size") or {}).get("price_monthly")
    public = [
        network.get("ip_address")
        for network in (item.get("networks") or {}).get("v4") or []
        if network.get("type") == "public"
    ]
    return Droplet(
        id=str(item["id"]),
        name=item.get("name") or str(item["id"]),
        status=item.get("status") or "",
        price_monthly=Decimal(str(price)) if price is not None else None,
        region=(item.get("region") or {}).get("name") or "",
        public_ipv4=public[0] if public else "",
    )


def plan_id(droplet_id):
    return f"droplet-{droplet_id}"


def apply(provider, droplets, now):
    added = 0
    monthly = Decimal(0)
    next_invoice = _first_of_next_month(timezone.localdate(now))
    for droplet in droplets:
        server = match_server(provider, droplet.id, droplet.name, [droplet.public_ipv4])
        if server is None:
            server = Server(
                name=unused_server_name(droplet.name, provider),
                provider=provider,
                kind=Server.Kind.VPS,
            )
            added += 1
        server.external_id = droplet.id
        server.live_status = droplet.status
        server.host = server.host or droplet.public_ipv4
        server.location = server.location or droplet.region
        server.save()
        if droplet.price_monthly is not None:
            monthly += droplet.price_monthly
            # DigitalOcean bills droplets monthly, in arrears, on the 1st.
            sync_plan(
                provider,
                plan_id(droplet.id),
                server=server,
                name=f"Droplet {droplet.name}",
                values={
                    "cost": droplet.price_monthly,
                    "currency": "USD",
                    "billing_cycle": Subscription.BillingCycle.MONTHLY,
                    "next_renewal": next_invoice,
                    "auto_renew": True,
                    "active": True,
                },
            )

    current = [droplet.id for droplet in droplets]
    gone = Server.objects.filter(provider=provider).exclude(external_id="")
    gone = gone.exclude(external_id__in=current)
    missing = gone.count()
    gone.update(live_status=MISSING)
    Subscription.objects.filter(provider=provider, external_id__startswith="droplet-").exclude(
        external_id__in=[plan_id(droplet_id) for droplet_id in current]
    ).update(active=False)

    summary = f"{plural(len(droplets), 'droplet')}, {monthly:.2f} USD a month"
    if added:
        summary += f" ({added} added)"
    if missing:
        summary += f"; {plural(missing, 'server')} no longer at DigitalOcean"
    return summary


def _first_of_next_month(today):
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)
