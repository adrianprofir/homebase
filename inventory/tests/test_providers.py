import importlib
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

import httpx
import pytest
from django.apps import apps as django_apps
from django.core.management import CommandError, call_command
from django.utils import timezone

from inventory import providers
from inventory.models import DnsRecord, Domain, Provider, Server, Subscription, Tunnel
from inventory.providers import INTEGRATIONS, due_integrations, sync

from .fake_api import FakeApi, cloudflare_page

pytestmark = pytest.mark.django_db

CF = "/client/v4"


@pytest.fixture
def tokens(settings):
    settings.CLOUDFLARE_API_TOKEN = "cf-token"
    settings.GODADDY_API_TOKEN = "gd-token"
    settings.DIGITALOCEAN_API_TOKEN = "do-token"
    settings.HOSTINGER_API_TOKEN = "hs-token"
    settings.CLOUDFLARE_ACCOUNT_ID = ""
    return settings


@pytest.fixture
def use_api(monkeypatch):
    """Route every provider client the code builds to `api`, for command-level tests."""

    def install(api):
        real = providers.http_client
        monkeypatch.setattr(
            providers,
            "http_client",
            lambda integration, transport=None: real(integration, api.transport),
        )

    return install


def run(key, api, now=None):
    return sync(INTEGRATIONS[key], now=now, transport=api.transport)


def assert_read_only(api, token):
    assert api.methods == {"GET"}
    assert {r.headers["Authorization"] for r in api.requests} == {f"Bearer {token}"}


# --- Cloudflare -----------------------------------------------------------------------


def cloudflare_api(records=None, tunnels=None, zones=None):
    zones = zones or [
        {"id": "z1", "name": "example.com", "status": "active", "account": {"id": "acct1"}},
        {"id": "z2", "name": "pending.net", "status": "pending", "account": {"id": "acct1"}},
    ]
    if records is None:
        records = [
            {
                "id": "r1",
                "type": "A",
                "name": "example.com",
                "content": "203.0.113.10",
                "proxied": True,
                "ttl": 1,
            },
            {
                "id": "r2",
                "type": "CNAME",
                "name": "www.example.com",
                "content": "example.com",
                "proxied": True,
                "ttl": 1,
            },
        ]
    if tunnels is None:
        tunnels = [
            {
                "id": "t1",
                "name": "home",
                "status": "healthy",
                "conns_active_at": "2026-10-01T08:00:00Z",
                "conns_inactive_at": None,
            }
        ]

    def zones_page(request):
        page = int(request.url.params["page"])
        return cloudflare_page(zones[page - 1 : page], page=page, total_pages=len(zones))

    def zone_records(zone_records):
        return lambda request: cloudflare_page(zone_records)

    return FakeApi(
        {
            f"{CF}/zones": zones_page,
            f"{CF}/zones/z1/dns_records": zone_records(records),
            f"{CF}/zones/z2/dns_records": zone_records([]),
            f"{CF}/zones/z3/dns_records": zone_records([]),
            f"{CF}/accounts/acct1/cfd_tunnel": cloudflare_page(tunnels),
        }
    )


class TestCloudflare:
    def test_syncs_zones_records_and_tunnels(self, tokens, godaddy):
        domain = Domain.objects.create(
            name="example.com", registrar=godaddy, expires_on=date(2027, 1, 1)
        )
        api = cloudflare_api()

        provider = run("cloudflare", api)

        assert provider.integration == "cloudflare"
        assert provider.name == "Cloudflare"
        assert provider.sync_error == ""
        assert provider.sync_succeeded_at is not None
        assert provider.sync_summary == (
            "2 zones, 2 DNS records, 1 tunnel; zones not in the inventory: pending.net"
        )
        domain.refresh_from_db()
        assert domain.dns_provider == provider
        assert domain.dns_zone_status == "active"
        assert sorted(domain.dns_records.values_list("name", "record_type", "content")) == [
            ("example.com", "A", "203.0.113.10"),
            ("www.example.com", "CNAME", "example.com"),
        ]
        tunnel = Tunnel.objects.get()
        assert (tunnel.name, tunnel.status, tunnel.provider) == ("home", "healthy", provider)
        assert tunnel.status_changed_at.isoformat() == "2026-10-01T08:00:00+00:00"
        assert api.params(f"{CF}/zones") == [
            {"page": "1", "per_page": "50"},
            {"page": "2", "per_page": "50"},
        ]
        assert api.params(f"{CF}/accounts/acct1/cfd_tunnel")[0]["is_deleted"] == "false"
        assert_read_only(api, "cf-token")

    def test_links_the_existing_provider_entered_by_hand(self, tokens, cloudflare):
        provider = run("cloudflare", cloudflare_api())

        assert provider.pk == cloudflare.pk
        assert Provider.objects.filter(integration="cloudflare").count() == 1

    def test_resync_replaces_records_and_removes_deleted_tunnels(self, tokens, godaddy):
        domain = Domain.objects.create(
            name="example.com", registrar=godaddy, expires_on=date(2027, 1, 1)
        )
        run("cloudflare", cloudflare_api())

        record = {
            "id": "r9",
            "type": "MX",
            "name": "example.com",
            "content": "mail.example.com",
            "proxied": None,
            "ttl": 300,
        }
        run("cloudflare", cloudflare_api(records=[record], tunnels=[]))

        assert list(domain.dns_records.values_list("external_id", flat=True)) == ["r9"]
        assert not Tunnel.objects.exists()

    def test_zone_that_disappears_is_marked_missing_and_loses_its_records(self, tokens, godaddy):
        domain = Domain.objects.create(
            name="example.com", registrar=godaddy, expires_on=date(2027, 1, 1)
        )
        run("cloudflare", cloudflare_api())
        other = [{"id": "z3", "name": "other.org", "status": "active", "account": {"id": "acct1"}}]

        run("cloudflare", cloudflare_api(zones=other))

        domain.refresh_from_db()
        assert domain.dns_zone_status == "missing"
        assert not DnsRecord.objects.exists()

    def test_api_error_is_recorded_and_keeps_the_previous_data(self, tokens, godaddy):
        domain = Domain.objects.create(
            name="example.com", registrar=godaddy, expires_on=date(2027, 1, 1)
        )
        first = run("cloudflare", cloudflare_api())
        api = cloudflare_api()
        api.routes[f"{CF}/zones/z1/dns_records"] = httpx.Response(
            403,
            json={"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]},
        )

        provider = run("cloudflare", api, now=timezone.now() + timedelta(hours=1))

        assert provider.sync_error == "HTTP 403 from zones/z1/dns_records: Authentication error"
        assert provider.sync_succeeded_at == first.sync_succeeded_at
        assert provider.sync_attempted_at > first.sync_attempted_at
        assert provider.sync_summary == first.sync_summary
        assert domain.dns_records.count() == 2

    def test_unsuccessful_envelope_is_an_error(self, tokens):
        api = FakeApi({f"{CF}/zones": {"success": False, "errors": [{"message": "nope"}]}})

        provider = run("cloudflare", api)

        assert provider.sync_error.startswith("zones was not successful")

    def test_account_id_setting_lists_tunnels_without_zones(self, tokens):
        tokens.CLOUDFLARE_ACCOUNT_ID = "acct1"
        api = cloudflare_api(zones=[])
        api.routes[f"{CF}/zones"] = cloudflare_page([], total_pages=0)

        provider = run("cloudflare", api)

        assert provider.sync_summary == "0 zones, 0 DNS records, 1 tunnel"


# --- GoDaddy --------------------------------------------------------------------------


def godaddy_domain(name, status="ACTIVE", expires="2027-03-01T23:59:59.000Z", renew=True):
    return {"domain": name, "status": status, "expires": expires, "renewAuto": renew}


class TestGoDaddy:
    def test_creates_and_updates_domains_and_their_registrations(self, tokens, godaddy):
        hostinger = Provider.objects.create(name="Hostinger")
        moved = Domain.objects.create(
            name="example.com", registrar=hostinger, expires_on=date(2026, 1, 1)
        )
        registration = Subscription.objects.create(
            name="example.com registration",
            provider=godaddy,
            cost=Decimal("20"),
            next_renewal=date(2026, 1, 1),
            domain=moved,
        )
        lapsed = Domain.objects.create(
            name="lapsed.example", registrar=godaddy, expires_on=date(2026, 2, 1)
        )
        api = FakeApi(
            {
                "/v1/domains": [
                    godaddy_domain("Example.com"),
                    godaddy_domain("new.dev", expires="2026-12-31T00:00:00Z", renew=False),
                    godaddy_domain("gone.com", status="TRANSFERRED_OUT"),
                ]
            }
        )

        provider = run("godaddy", api)

        assert provider.pk == godaddy.pk
        assert provider.sync_summary == (
            "2 domains (1 added); not in the account any more: lapsed.example"
        )
        moved.refresh_from_db()
        assert (moved.registrar, moved.expires_on, moved.auto_renew) == (
            godaddy,
            date(2027, 3, 1),
            True,
        )
        assert moved.registrar_status == "ACTIVE"
        registration.refresh_from_db()
        assert (registration.next_renewal, registration.auto_renew) == (date(2027, 3, 1), True)
        new = Domain.objects.get(name="new.dev")
        assert (new.registrar, new.expires_on, new.auto_renew) == (
            godaddy,
            date(2026, 12, 31),
            False,
        )
        assert not Domain.objects.filter(name="gone.com").exists()
        lapsed.refresh_from_db()
        assert lapsed.registrar_status == "missing"
        assert api.params("/v1/domains") == [{"limit": "100"}]
        assert_read_only(api, "gd-token")

    def test_transferred_out_domain_keeps_its_dates(self, tokens, godaddy):
        domain = Domain.objects.create(
            name="old.example", registrar=godaddy, expires_on=date(2026, 5, 1)
        )
        api = FakeApi({"/v1/domains": [godaddy_domain("old.example", status="TRANSFERRED_OUT")]})

        run("godaddy", api)

        domain.refresh_from_db()
        assert domain.registrar_status == "TRANSFERRED_OUT"
        assert domain.expires_on == date(2026, 5, 1)

    def test_pages_through_with_a_marker(self, tokens, monkeypatch):
        monkeypatch.setattr("inventory.providers.godaddy.PAGE_SIZE", 2)
        pages = {
            None: [godaddy_domain("a.example"), godaddy_domain("b.example")],
            "b.example": [godaddy_domain("c.example")],
        }
        api = FakeApi({"/v1/domains": lambda r: pages[r.url.params.get("marker")]})

        provider = run("godaddy", api)

        assert provider.sync_summary == "3 domains (3 added)"
        assert [p.get("marker") for p in api.params("/v1/domains")] == [None, "b.example"]

    def test_forbidden_account_shows_the_reason(self, tokens):
        api = FakeApi(
            {
                "/v1/domains": httpx.Response(
                    403,
                    json={
                        "code": "ACCESS_DENIED",
                        "message": "Authenticated user is not allowed access",
                    },
                )
            }
        )

        provider = run("godaddy", api)

        assert provider.sync_error == (
            "HTTP 403 from v1/domains: Authenticated user is not allowed access"
        )


# --- DigitalOcean ---------------------------------------------------------------------


def droplet(id, name, ip, price=6, status="active"):
    return {
        "id": id,
        "name": name,
        "status": status,
        "size": {"slug": "s-1vcpu-1gb", "price_monthly": price},
        "region": {"slug": "fra1", "name": "Frankfurt 1"},
        "networks": {
            "v4": [
                {"ip_address": "10.0.0.5", "type": "private"},
                {"ip_address": ip, "type": "public"},
            ]
        },
    }


class TestDigitalOcean:
    @pytest.mark.parametrize("name", ["web", "w" * 100, "w" * 150])
    def test_repeated_recreation_retains_unique_bounded_names(self, tokens, name):
        for ident in range(1, 6):
            provider = run(
                "digitalocean",
                FakeApi({"/v2/droplets": {"droplets": [droplet(ident, name, "203.0.113.5")]}}),
            )
            assert not provider.sync_error
            server = Server.objects.get(external_id=str(ident))
            assert len(server.name) <= 100
            assert len(server.subscriptions.get().name) <= 150
            run("digitalocean", FakeApi({"/v2/droplets": {"droplets": []}}))
        names = list(Server.objects.values_list("name", flat=True))
        assert len(names) == len(set(names)) == 5

    def test_matches_and_creates_servers_with_their_monthly_plan(self, tokens, digitalocean):
        manual = Server.objects.create(
            name="web", provider=digitalocean, kind=Server.Kind.VPS, host="203.0.113.5"
        )
        manual_plan = Subscription.objects.create(
            name="Web droplet",
            provider=digitalocean,
            cost=Decimal("4"),
            billing_cycle=Subscription.BillingCycle.MONTHLY,
            next_renewal=date(2026, 1, 1),
            server=manual,
        )
        pages = {
            "1": {
                "droplets": [droplet(101, "web-1", "203.0.113.5")],
                "links": {"pages": {"next": "https://api.digitalocean.com/v2/droplets?page=2"}},
            },
            "2": {
                "droplets": [droplet(102, "db-1", "203.0.113.6", price=12, status="off")],
                "links": {},
            },
        }
        api = FakeApi({"/v2/droplets": lambda r: pages[r.url.params["page"]]})
        now = timezone.make_aware(timezone.datetime(2026, 12, 15, 12, 0))

        provider = run("digitalocean", api, now=now)

        assert provider.sync_summary == "2 droplets, 18.00 USD a month (1 added)"
        manual.refresh_from_db()
        assert (manual.external_id, manual.live_status, manual.location) == (
            "101",
            "active",
            "Frankfurt 1",
        )
        manual_plan.refresh_from_db()
        assert manual_plan.name == "Web droplet"
        assert (manual_plan.external_id, manual_plan.cost) == ("droplet-101", Decimal("6"))
        assert manual_plan.next_renewal == date(2027, 1, 1)
        db = Server.objects.get(external_id="102")
        assert (db.name, db.host, db.live_status, db.kind) == ("db-1", "203.0.113.6", "off", "vps")
        plan = db.subscriptions.get()
        assert (plan.name, plan.cost, plan.currency, plan.billing_cycle, plan.auto_renew) == (
            "Droplet db-1",
            Decimal("12"),
            "USD",
            Subscription.BillingCycle.MONTHLY,
            True,
        )
        assert api.params("/v2/droplets")[0] == {"page": "1", "per_page": "200"}
        assert_read_only(api, "do-token")

    def test_deleted_droplet_marks_server_missing_and_stops_its_plan(self, tokens):
        run(
            "digitalocean",
            FakeApi({"/v2/droplets": {"droplets": [droplet(101, "web-1", "203.0.113.5")]}}),
        )

        provider = run("digitalocean", FakeApi({"/v2/droplets": {"droplets": []}}))

        server = Server.objects.get(external_id="101")
        assert server.live_status == "missing"
        assert server.subscriptions.get().active is False
        assert provider.sync_summary == (
            "0 droplets, 0.00 USD a month; 1 server no longer at DigitalOcean"
        )

    def test_name_clash_with_another_server_gets_a_suffix(self, tokens):
        Server.objects.create(name="web-1", kind=Server.Kind.HOME)

        run(
            "digitalocean",
            FakeApi({"/v2/droplets": {"droplets": [droplet(101, "web-1", "203.0.113.5")]}}),
        )

        assert Server.objects.get(external_id="101").name == "web-1 (DigitalOcean)"


# --- Hostinger ------------------------------------------------------------------------


def hostinger_plan(id, name, **fields):
    return {
        "id": id,
        "name": name,
        "status": "active",
        "billing_period": 12,
        "billing_period_unit": "month",
        "currency_code": "EUR",
        "total_price": 8388,
        "renewal_price": 11988,
        "is_auto_renewed": True,
        "created_at": "2025-03-01T10:00:00Z",
        "expires_at": "2027-03-01T10:00:00Z",
        "next_billing_at": "2027-02-20T10:00:00Z",
        **fields,
    }


def hostinger_api(machines, plans):
    return FakeApi(
        {"/api/vps/v1/virtual-machines": machines, "/api/billing/v1/subscriptions": plans}
    )


VM = {
    "id": 17923,
    "subscription_id": "AzzVPS",
    "plan": "KVM 2",
    "hostname": "srv000001.example",
    "state": "running",
    "ipv4": [{"id": 1, "address": "198.51.100.7", "ptr": None}],
}


class TestHostinger:
    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ({"next_billing_at": None}, date(2027, 3, 1)),
            ({"expires_at": None}, date(2027, 2, 20)),
            ({"is_auto_renewed": False}, date(2027, 3, 1)),
            ({"status": "not_renewing"}, date(2027, 3, 1)),
        ],
    )
    def test_renewal_date_tracks_billing_or_expiry(self, tokens, fields, expected):
        provider = run("hostinger", hostinger_api([], [hostinger_plan("web", "Web", **fields)]))

        assert not provider.sync_error
        assert Subscription.objects.get(external_id="web").next_renewal == expected

    def test_repeated_vm_recreation_retains_unique_bounded_names(self, tokens):
        for ident in range(1, 6):
            provider = run(
                "hostinger", hostinger_api([{**VM, "id": ident, "hostname": "w" * 100}], [])
            )
            assert not provider.sync_error
            assert len(Server.objects.get(external_id=str(ident)).name) <= 100
            run("hostinger", hostinger_api([], []))
        assert Server.objects.count() == 5

    def test_syncs_vps_state_and_every_billing_subscription(self, tokens):
        api = hostinger_api(
            [VM],
            [
                hostinger_plan("AzzVPS", "KVM 2"),
                hostinger_plan(
                    "AzzWEB", "Premium Web Hosting", billing_period=4, billing_period_unit="year"
                ),
                hostinger_plan("AzzOLD", "Old plan", status="cancelled"),
                hostinger_plan("AzzODD", "Odd plan", billing_period=7, billing_period_unit="day"),
            ],
        )

        provider = run("hostinger", api)

        assert provider.sync_summary == (
            "1 VPS, 3 active subscriptions (1 server and 2 subscriptions added); "
            "no price or renewal date for: Odd plan"
        )
        server = Server.objects.get(external_id="17923")
        assert (server.name, server.host, server.live_status, server.provider) == (
            "srv000001.example",
            "198.51.100.7",
            "running",
            provider,
        )
        vps = Subscription.objects.get(external_id="AzzVPS")
        assert (vps.server, vps.cost, vps.currency, vps.billing_cycle) == (
            server,
            Decimal("119.88"),
            "EUR",
            12,
        )
        assert (vps.next_renewal, vps.auto_renew, vps.active) == (date(2027, 2, 20), True, True)
        web = Subscription.objects.get(external_id="AzzWEB")
        assert (web.server, web.billing_cycle) == (None, Subscription.BillingCycle.QUADRENNIAL)
        assert not Subscription.objects.filter(external_id__in=["AzzOLD", "AzzODD"]).exists()
        assert_read_only(api, "hs-token")

    def test_adopts_the_plan_entered_by_hand_for_the_server(self, tokens):
        hostinger = Provider.objects.create(name="Hostinger")
        server = Server.objects.create(
            name="vps", provider=hostinger, kind=Server.Kind.VPS, host="198.51.100.7"
        )
        manual = Subscription.objects.create(
            name="Hostinger VPS",
            provider=hostinger,
            cost=Decimal("5"),
            next_renewal=date(2026, 1, 1),
            server=server,
            notes="bought on sale",
        )

        run(
            "hostinger",
            hostinger_api([VM], [hostinger_plan("AzzVPS", "KVM 2", status="not_renewing")]),
        )

        manual.refresh_from_db()
        assert (manual.external_id, manual.name, manual.notes) == (
            "AzzVPS",
            "Hostinger VPS",
            "bought on sale",
        )
        assert (manual.cost, manual.auto_renew) == (Decimal("119.88"), False)
        assert Subscription.objects.count() == 1

    def test_plan_that_disappears_is_deactivated(self, tokens):
        run("hostinger", hostinger_api([], [hostinger_plan("AzzWEB", "Web")]))

        run("hostinger", hostinger_api([], []))

        assert Subscription.objects.get(external_id="AzzWEB").active is False


# --- Runner and commands --------------------------------------------------------------


class TestSyncRunner:
    def test_unexpected_error_rolls_back_and_is_recorded(self, tokens, godaddy, monkeypatch):
        Domain.objects.create(name="example.com", registrar=godaddy, expires_on=date(2027, 1, 1))

        def explode(*args):
            Domain.objects.all().delete()
            raise RuntimeError("boom")

        monkeypatch.setattr("inventory.providers.cloudflare.apply", explode)

        provider = run("cloudflare", cloudflare_api())

        assert provider.sync_error == "Unexpected error: RuntimeError: boom"
        assert Domain.objects.filter(name="example.com").exists()

    def test_timeout_is_reported(self, tokens):
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        provider = run("cloudflare", FakeApi({f"{CF}/zones": slow}))

        assert provider.sync_error == "Timed out calling zones"

    def test_non_json_body_is_reported(self, tokens):
        api = FakeApi({"/v2/droplets": httpx.Response(200, text="<html>maintenance</html>")})

        assert run("digitalocean", api).sync_error == "droplets did not return JSON"

    def test_due_integrations_follow_tokens_and_interval(self, settings):
        settings.CLOUDFLARE_API_TOKEN = "cf"
        settings.GODADDY_API_TOKEN = "gd"
        settings.DIGITALOCEAN_API_TOKEN = ""
        settings.HOSTINGER_API_TOKEN = ""
        settings.HOMEBASE_SYNC_INTERVAL_MINUTES = 60
        now = timezone.now()
        Provider.objects.create(
            name="Cloudflare",
            integration="cloudflare",
            sync_attempted_at=now - timedelta(minutes=10),
        )
        Provider.objects.create(
            name="GoDaddy", integration="godaddy", sync_attempted_at=now - timedelta(minutes=61)
        )

        assert [i.key for i in due_integrations(now)] == ["godaddy"]


class TestSyncProvidersCommand:
    def test_skips_providers_without_a_token(self, settings):
        for key in INTEGRATIONS.values():
            setattr(settings, key.token_setting, "")
        out = StringIO()

        call_command("sync_providers", stdout=out)

        assert "skip  Cloudflare (CLOUDFLARE_API_TOKEN is not set)" in out.getvalue()
        assert not Provider.objects.exists()

    def test_reports_results_and_fails_loudly(self, tokens, use_api):
        use_api(FakeApi({"/v2/droplets": {"droplets": [droplet(1, "web", "203.0.113.5")]}}))
        out = StringIO()

        with pytest.raises(CommandError, match="1 provider sync"):
            call_command(
                "sync_providers", "--provider", "digitalocean", "--provider", "godaddy", stdout=out
            )

        text = out.getvalue()
        assert "ok    DigitalOcean: 1 droplet, 6.00 USD a month (1 added)" in text
        assert "fail  GoDaddy: HTTP 404 from v1/domains" in text


def test_migration_links_providers_entered_by_hand(db):
    migration = importlib.import_module("inventory.migrations.0004_link_provider_integrations")
    Provider.objects.create(name="Digital Ocean")
    Provider.objects.create(name="GoDaddy")
    Provider.objects.create(name="Linode")

    migration.link_integrations(django_apps, None)

    assert dict(Provider.objects.values_list("name", "integration")) == {
        "Digital Ocean": "digitalocean",
        "GoDaddy": "godaddy",
        "Linode": "",
    }
