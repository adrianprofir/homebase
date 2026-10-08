"""The fictional household lab that `seed_demo` builds and demo mode simulates.

Everything here is invented. Domains use the reserved `.example` TLD, addresses come
from the RFC 5737 documentation ranges, and every date is a number of days from today,
so the dashboard always has renewals coming up, one domain about to lapse without
auto-renew, one TLS certificate inside the warning window, and one app down.

This module is plain data so the seed, the simulated checks, and the canned provider
API all describe the same lab.
"""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class DemoProvider:
    name: str
    website: str
    integration: str = ""
    account_notes: str = ""


@dataclass(frozen=True)
class DemoDomain:
    name: str
    registrar: str
    expires_in_days: int
    auto_renew: bool
    # The registration fee, as a yearly subscription at the registrar.
    yearly_cost: Decimal
    currency: str = "USD"
    notes: str = ""


@dataclass(frozen=True)
class DemoServer:
    name: str
    kind: str
    host: str
    location: str
    provider: str = ""
    notes: str = ""
    # The status the provider last reported, for servers whose sync fails in the demo.
    live_status: str = ""


@dataclass(frozen=True)
class DemoSubscription:
    name: str
    provider: str
    cost: Decimal
    currency: str
    billing_cycle: int
    # None: the 1st of next month, for plans billed monthly in arrears.
    renews_in_days: int | None
    auto_renew: bool
    server: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Outage:
    """A past outage in the uptime history, `days_ago` days before today."""

    days_ago: int
    hour: int
    minute: int
    minutes: int
    # None: the request timed out, so there is no HTTP status.
    status_code: int | None


@dataclass(frozen=True)
class DemoApp:
    name: str
    server: str
    url: str = ""
    hostname: str = ""
    status: str = "live"
    repo_url: str = ""
    notes: str = ""
    # How the simulated checks answer.
    response_ms: int = 120
    tls_days: int = 60
    outages: tuple[Outage, ...] = ()
    # Down with this HTTP status since `down_minutes` before the seed ran, and from then on.
    down_status: int | None = None
    down_minutes: int = 0


@dataclass(frozen=True)
class DemoRecord:
    type: str
    name: str
    content: str
    proxied: bool | None = None
    ttl: int = 1


@dataclass(frozen=True)
class DemoZone:
    id: str
    name: str
    status: str
    records: tuple[DemoRecord, ...] = field(default=())


@dataclass(frozen=True)
class DemoDroplet:
    id: int
    name: str
    status: str
    price_monthly: Decimal
    region: str
    ipv4: str


HOME_SERVER = "homelab"
TUNNEL_TARGET = "6f1c2b9e-7d3a-4e5f-9a1b-2c3d4e5f6a7b.cfargotunnel.com"

PROVIDERS = [
    DemoProvider(
        "Backblaze",
        "https://www.backblaze.com",
        account_notes="Login: admin@fernhill.example. Nightly photo backups go to B2.",
    ),
    DemoProvider(
        "Cloudflare",
        "https://www.cloudflare.com",
        integration="cloudflare",
        account_notes="Login: admin@fernhill.example. DNS and the tunnel to the home server.",
    ),
    DemoProvider(
        "DigitalOcean",
        "https://www.digitalocean.com",
        integration="digitalocean",
        account_notes="Team: Fernhill. Droplets for the public edge and backups.",
    ),
    DemoProvider(
        "GoDaddy",
        "https://www.godaddy.com",
        integration="godaddy",
        account_notes="Customer number in the password manager.",
    ),
    DemoProvider(
        "Hostinger",
        "https://www.hostinger.com",
        integration="hostinger",
        account_notes="Mail VPS and business email.",
    ),
]

DOMAINS = [
    DemoDomain("fernhill.example", "GoDaddy", 47, True, Decimal("21.99")),
    DemoDomain(
        "fernhill-photos.example",
        "GoDaddy",
        6,
        False,
        Decimal("21.99"),
        notes="Short link for sharing albums. Decide whether to keep it.",
    ),
    DemoDomain("quietbrook.example", "GoDaddy", 33, True, Decimal("21.99")),
    DemoDomain("kitchen-notes.example", "GoDaddy", 212, True, Decimal("18.99")),
    DemoDomain("fernhill-mail.example", "Hostinger", 75, True, Decimal("15.99"), currency="EUR"),
]

SERVERS = [
    DemoServer(
        HOME_SERVER,
        "home",
        "192.0.2.10",
        "Home, hall cupboard",
        notes="Mini PC running Docker Compose. Reached from outside through the tunnel.",
    ),
    DemoServer("edge-1", "vps", "198.51.100.21", "", provider="DigitalOcean"),
    DemoServer("backup-relay", "vps", "198.51.100.34", "", provider="DigitalOcean"),
    DemoServer(
        "mail-vps",
        "vps",
        "203.0.113.40",
        "Frankfurt",
        provider="Hostinger",
        live_status="running",
    ),
]

SUBSCRIPTIONS = [
    DemoSubscription(
        "KVM 2 VPS",
        "Hostinger",
        Decimal("95.88"),
        "EUR",
        12,
        renews_in_days=23,
        auto_renew=True,
        server="mail-vps",
    ),
    DemoSubscription(
        "Business Email",
        "Hostinger",
        Decimal("35.88"),
        "EUR",
        12,
        renews_in_days=19,
        auto_renew=False,
        notes="Three mailboxes on fernhill-mail.example.",
    ),
    DemoSubscription(
        "B2 Cloud Storage",
        "Backblaze",
        Decimal("6.00"),
        "USD",
        1,
        renews_in_days=None,
        auto_renew=True,
        notes="About 1 TB of photo backups.",
    ),
]

APPS = [
    DemoApp(
        "Photo library",
        HOME_SERVER,
        url="https://photos.fernhill.example/",
        hostname="photos.fernhill.example",
        repo_url="https://git.fernhill.example/fernhill/photos",
        response_ms=180,
        tls_days=64,
        outages=(Outage(days_ago=4, hour=2, minute=10, minutes=70, status_code=None),),
    ),
    DemoApp(
        "Recipe box",
        HOME_SERVER,
        url="https://recipes.fernhill.example/",
        hostname="recipes.fernhill.example",
        response_ms=95,
        tls_days=64,
        outages=(Outage(days_ago=11, hour=19, minute=35, minutes=25, status_code=503),),
    ),
    DemoApp(
        "Media server",
        HOME_SERVER,
        url="https://media.fernhill.example/",
        hostname="media.fernhill.example",
        response_ms=240,
        tls_days=64,
    ),
    DemoApp(
        "Password vault",
        HOME_SERVER,
        url="https://vault.fernhill.example/",
        hostname="vault.fernhill.example",
        response_ms=130,
        tls_days=6,
        notes="Its certificate is issued by hand. Renew it before it expires.",
    ),
    DemoApp(
        "Home automation",
        HOME_SERVER,
        url="http://192.0.2.10:8123/",
        response_ms=40,
        notes="LAN only.",
    ),
    DemoApp(
        "Status page",
        "edge-1",
        url="https://status.fernhill.example/",
        hostname="status.fernhill.example",
        response_ms=60,
        tls_days=80,
    ),
    DemoApp(
        "Wiki",
        "edge-1",
        url="https://wiki.quietbrook.example/",
        hostname="wiki.quietbrook.example",
        response_ms=150,
        tls_days=51,
        down_status=502,
        down_minutes=35,
    ),
    DemoApp(
        "Webmail",
        "mail-vps",
        url="https://mail.fernhill-mail.example/",
        hostname="mail.fernhill-mail.example",
        response_ms=310,
        tls_days=70,
    ),
    DemoApp(
        "Budget planner",
        HOME_SERVER,
        status="development",
        repo_url="https://git.fernhill.example/fernhill/budget",
        notes="Not deployed yet.",
    ),
]

# What the canned Cloudflare API reports.
CLOUDFLARE_ACCOUNT_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
CLOUDFLARE_ZONES = [
    DemoZone(
        "1f0e2d3c4b5a69788796a5b4c3d2e1f0",
        "fernhill.example",
        "active",
        (
            DemoRecord("A", "fernhill.example", "198.51.100.21", proxied=True),
            DemoRecord("CNAME", "www.fernhill.example", "fernhill.example", proxied=True),
            DemoRecord("CNAME", "photos.fernhill.example", TUNNEL_TARGET, proxied=True),
            DemoRecord("CNAME", "recipes.fernhill.example", TUNNEL_TARGET, proxied=True),
            DemoRecord("CNAME", "media.fernhill.example", TUNNEL_TARGET, proxied=True),
            DemoRecord("CNAME", "vault.fernhill.example", TUNNEL_TARGET, proxied=True),
            DemoRecord("CNAME", "status.fernhill.example", "fernhill.example", proxied=True),
            DemoRecord("MX", "fernhill.example", "mail.fernhill-mail.example", ttl=3600),
            DemoRecord(
                "TXT", "fernhill.example", '"v=spf1 include:fernhill-mail.example ~all"', ttl=3600
            ),
        ),
    ),
    DemoZone(
        "2e1f0d3c4b5a69788796a5b4c3d2e1f1",
        "fernhill-photos.example",
        "active",
        (DemoRecord("CNAME", "fernhill-photos.example", "photos.fernhill.example", proxied=True),),
    ),
    DemoZone(
        "3d2e1f0c4b5a69788796a5b4c3d2e1f2",
        "quietbrook.example",
        "pending",
        (DemoRecord("A", "wiki.quietbrook.example", "198.51.100.21", proxied=False, ttl=300),),
    ),
]
CLOUDFLARE_TUNNEL = {
    "id": "6f1c2b9e-7d3a-4e5f-9a1b-2c3d4e5f6a7b",
    "name": "homelab",
    "connected_days_ago": 3,
}

# What the canned DigitalOcean API reports. The seed enters these servers by hand;
# the first sync links them by name and adds their plans.
DROPLETS = [
    DemoDroplet(401000001, "edge-1", "active", Decimal("6.00"), "Amsterdam 3", "198.51.100.21"),
    DemoDroplet(401000002, "backup-relay", "off", Decimal("4.00"), "Frankfurt 1", "198.51.100.34"),
]

# Hostinger fails in the demo, as if its token had been revoked, so the error state and
# the sync alert are on show. Its data comes from the seed and an earlier good sync.
HOSTINGER_ERROR = {"message": "Unauthenticated."}
HOSTINGER_LAST_GOOD_SYNC_DAYS_AGO = 2
HOSTINGER_LAST_SUMMARY = "1 VPS, 3 active subscriptions"


def app_for_url(url):
    return next((app for app in APPS if app.url and app.url == url), None)
