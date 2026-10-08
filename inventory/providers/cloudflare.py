"""Cloudflare: zones, their DNS records, and Cloudflare Tunnels.

Token permissions: Zone Read and DNS Read on the zones, Cloudflare Tunnel Read on the account.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from inventory.models import DnsRecord, Domain, Tunnel

from .base import MISSING, ProviderError, get_json, plural

MAX_PAGES = 100


@dataclass(frozen=True)
class Record:
    id: str
    type: str
    name: str
    content: str
    proxied: bool | None
    ttl: int


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    status: str
    account_id: str
    records: list[Record]


@dataclass(frozen=True)
class TunnelInfo:
    id: str
    name: str
    status: str
    status_changed_at: datetime | None


@dataclass(frozen=True)
class Snapshot:
    zones: list[Zone]
    tunnels: list[TunnelInfo]


def fetch(http):
    zones = [
        Zone(
            id=zone["id"],
            name=zone["name"].lower(),
            status=zone.get("status") or "",
            account_id=(zone.get("account") or {}).get("id") or "",
            records=[_record(r) for r in _paged(http, f"zones/{zone['id']}/dns_records", 5000)],
        )
        for zone in _paged(http, "zones", 50)
    ]
    account_ids = {zone.account_id for zone in zones if zone.account_id}
    if settings.CLOUDFLARE_ACCOUNT_ID:
        account_ids.add(settings.CLOUDFLARE_ACCOUNT_ID)
    tunnels = [
        _tunnel(tunnel)
        for account_id in sorted(account_ids)
        for tunnel in _paged(http, f"accounts/{account_id}/cfd_tunnel", 1000, is_deleted="false")
    ]
    return Snapshot(zones=zones, tunnels=tunnels)


def _paged(http, path, per_page, **params):
    """Every result of a paginated Cloudflare list endpoint."""
    for page in range(1, MAX_PAGES + 1):
        body = get_json(http, path, {**params, "page": page, "per_page": per_page})
        if not isinstance(body, dict) or not body.get("success"):
            errors = body.get("errors") if isinstance(body, dict) else None
            raise ProviderError(f"{path} was not successful: {errors or 'no details'}")
        yield from body.get("result") or []
        info = body.get("result_info") or {}
        total_pages = info.get("total_pages") or math.ceil(info.get("total_count", 0) / per_page)
        if page >= total_pages:
            return
    raise ProviderError(f"{path} has more than {MAX_PAGES} pages")


def _record(record):
    return Record(
        id=record["id"],
        type=record.get("type") or "",
        name=record.get("name") or "",
        content=str(record.get("content") or ""),
        proxied=record.get("proxied"),
        ttl=record.get("ttl") or 1,
    )


def _tunnel(tunnel):
    status = tunnel.get("status") or ""
    if status in ("healthy", "degraded"):
        changed = tunnel.get("conns_active_at")
    elif status == "down":
        changed = tunnel.get("conns_inactive_at")
    else:
        changed = None
    return TunnelInfo(
        id=tunnel["id"],
        name=tunnel.get("name") or tunnel["id"],
        status=status,
        status_changed_at=parse_datetime(changed) if changed else None,
    )


def apply(provider, snapshot, now):
    domains = {domain.name: domain for domain in Domain.objects.all()}
    seen = set()
    not_in_inventory = []
    records = 0
    for zone in snapshot.zones:
        domain = domains.get(zone.name)
        if domain is None:
            not_in_inventory.append(zone.name)
            continue
        seen.add(domain.pk)
        domain.dns_zone_status = zone.status
        if zone.status == "active":
            domain.dns_provider = provider
        domain.save(update_fields=["dns_zone_status", "dns_provider"])
        DnsRecord.objects.filter(domain=domain).delete()
        DnsRecord.objects.bulk_create(
            DnsRecord(
                domain=domain,
                external_id=record.id,
                record_type=record.type,
                name=record.name,
                content=record.content,
                proxied=record.proxied,
                ttl=record.ttl,
            )
            for record in zone.records
        )
        records += len(zone.records)

    # Zones that disappeared from the account take their records with them.
    gone = Domain.objects.filter(Q(dns_provider=provider) | ~Q(dns_zone_status="")).exclude(
        pk__in=seen
    )
    DnsRecord.objects.filter(domain__in=gone).delete()
    gone.filter(dns_provider=provider).update(dns_zone_status=MISSING)
    gone.exclude(dns_provider=provider).update(dns_zone_status="")

    for tunnel in snapshot.tunnels:
        Tunnel.objects.update_or_create(
            provider=provider,
            external_id=tunnel.id,
            defaults={
                "name": tunnel.name,
                "status": tunnel.status,
                "status_changed_at": tunnel.status_changed_at,
            },
        )
    Tunnel.objects.filter(provider=provider).exclude(
        external_id__in=[tunnel.id for tunnel in snapshot.tunnels]
    ).delete()

    summary = (
        f"{plural(len(snapshot.zones), 'zone')}, {plural(records, 'DNS record')}, "
        f"{plural(len(snapshot.tunnels), 'tunnel')}"
    )
    if not_in_inventory:
        summary += f"; zones not in the inventory: {', '.join(sorted(not_in_inventory))}"
    return summary
