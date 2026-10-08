"""Canned provider APIs for demo mode, served through `httpx.MockTransport`.

The real provider modules run unchanged against these responses, so the demo shows
exactly what a sync writes, without a token and without a single outbound request.
Hostinger answers 401 on purpose, so the failing-sync state is on show too.
"""

from datetime import UTC, datetime, time, timedelta

import httpx
from django.utils import timezone

from . import lab


def transport(now=None):
    """A transport that answers every provider API from the fictional lab."""
    return httpx.MockTransport(lambda request: respond(request, now or timezone.now()))


def respond(request, now):
    if request.method != "GET":
        return httpx.Response(405, json={"message": "The demo API is read-only."})
    host, path = request.url.host, request.url.path
    if host == "api.cloudflare.com":
        return _cloudflare(path, now)
    if host == "api.godaddy.com" and path == "/v1/domains":
        return httpx.Response(200, json=_godaddy_domains(now))
    if host == "api.digitalocean.com" and path == "/v2/droplets":
        return httpx.Response(200, json=_droplets())
    if host == "developers.hostinger.com":
        return httpx.Response(401, json=lab.HOSTINGER_ERROR)
    return httpx.Response(404, json={"message": f"No demo response for {host}{path}"})


def _cloudflare_page(result):
    return {
        "success": True,
        "errors": [],
        "messages": [],
        "result": result,
        "result_info": {"page": 1, "per_page": len(result), "total_pages": 1},
    }


def _cloudflare(path, now):
    prefix = "/client/v4/"
    path = path.removeprefix(prefix)
    if path == "zones":
        return httpx.Response(
            200,
            json=_cloudflare_page(
                [
                    {
                        "id": zone.id,
                        "name": zone.name,
                        "status": zone.status,
                        "account": {"id": lab.CLOUDFLARE_ACCOUNT_ID},
                    }
                    for zone in lab.CLOUDFLARE_ZONES
                ]
            ),
        )
    for zone in lab.CLOUDFLARE_ZONES:
        if path == f"zones/{zone.id}/dns_records":
            records = [
                {
                    "id": f"{zone.id[:24]}{index:08x}",
                    "type": record.type,
                    "name": record.name,
                    "content": record.content,
                    "proxied": record.proxied,
                    "ttl": record.ttl,
                }
                for index, record in enumerate(zone.records, start=1)
            ]
            return httpx.Response(200, json=_cloudflare_page(records))
    if path == f"accounts/{lab.CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel":
        tunnel = lab.CLOUDFLARE_TUNNEL
        connected = now - timedelta(days=tunnel["connected_days_ago"])
        return httpx.Response(
            200,
            json=_cloudflare_page(
                [
                    {
                        "id": tunnel["id"],
                        "name": tunnel["name"],
                        "status": "healthy",
                        "conns_active_at": _iso(connected),
                        "conns_inactive_at": None,
                    }
                ]
            ),
        )
    return httpx.Response(
        404, json={"success": False, "errors": [{"message": f"No demo response for {path}"}]}
    )


def _godaddy_domains(now):
    today = timezone.localdate(now)
    return [
        {
            "domain": domain.name,
            "status": "ACTIVE",
            # Midday UTC, so the expiry lands on the same calendar date in any time zone
            # the sync converts it from.
            "expires": _iso(
                datetime.combine(
                    today + timedelta(days=domain.expires_in_days), time(12), tzinfo=UTC
                )
            ),
            "renewAuto": domain.auto_renew,
        }
        for domain in lab.DOMAINS
        if domain.registrar == "GoDaddy"
    ]


def _droplets():
    return {
        "droplets": [
            {
                "id": droplet.id,
                "name": droplet.name,
                "status": droplet.status,
                "size": {"price_monthly": float(droplet.price_monthly)},
                "region": {"name": droplet.region},
                "networks": {"v4": [{"type": "public", "ip_address": droplet.ipv4}]},
            }
            for droplet in lab.DROPLETS
        ],
        "links": {},
        "meta": {"total": len(lab.DROPLETS)},
    }


def _iso(moment):
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
