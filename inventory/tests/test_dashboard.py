from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from inventory.alerts import update_alerts
from inventory.dashboard import Renewal, cost_totals, upcoming_renewals
from inventory.models import (
    App,
    AppCheck,
    DnsRecord,
    Domain,
    Provider,
    Server,
    Subscription,
    Tunnel,
)


def days_from_today(days):
    return timezone.localdate() + timedelta(days=days)


def make_subscription(provider, **fields):
    defaults = {
        "name": "Plan",
        "cost": Decimal("10.00"),
        "currency": "USD",
        "billing_cycle": Subscription.BillingCycle.YEARLY,
        "next_renewal": days_from_today(200),
    }
    return Subscription.objects.create(provider=provider, **{**defaults, **fields})


@pytest.fixture
def user_client(client, django_user_model):
    user = django_user_model.objects.create_user("alice", password="not-a-real-password")
    client.force_login(user)
    return client


class TestRenewal:
    @pytest.mark.parametrize(
        ("days_left", "label", "overdue", "soon"),
        [
            (-3, "3 days overdue", True, False),
            (-1, "1 day overdue", True, False),
            (0, "Due today", False, True),
            (1, "In 1 day", False, True),
            (14, "In 14 days", False, True),
            (15, "In 15 days", False, False),
        ],
    )
    def test_labels_and_flags(self, days_left, label, overdue, soon):
        renewal = Renewal(
            kind="Domain",
            name="example.com",
            provider="GoDaddy",
            due=date(2026, 1, 1),
            days_left=days_left,
            auto_renew=False,
            amount="",
            admin_url="/",
        )

        assert renewal.due_label == label
        assert renewal.overdue is overdue
        assert renewal.soon is soon


@pytest.mark.django_db
class TestUpcomingRenewals:
    def test_includes_overdue_and_window_items_sorted_by_date(self, godaddy):
        today = date(2026, 10, 4)
        Domain.objects.create(name="lapsed.example", registrar=godaddy, expires_on=date(2026, 9, 1))
        Domain.objects.create(name="soon.example", registrar=godaddy, expires_on=date(2026, 10, 10))
        Domain.objects.create(name="edge.example", registrar=godaddy, expires_on=date(2026, 12, 3))
        Domain.objects.create(name="later.example", registrar=godaddy, expires_on=date(2026, 12, 4))
        make_subscription(godaddy, name="Hosting", next_renewal=date(2026, 10, 5))
        make_subscription(godaddy, name="Cancelled", next_renewal=date(2026, 10, 5), active=False)

        renewals = upcoming_renewals(today, window_days=60)

        assert [(r.kind, r.name, r.days_left) for r in renewals] == [
            ("Domain", "lapsed.example", -33),
            ("Subscription", "Hosting", 1),
            ("Domain", "soon.example", 6),
            ("Domain", "edge.example", 60),
        ]
        assert renewals[1].amount == "10.00 USD"
        assert renewals[1].admin_url.endswith("/change/")


@pytest.mark.django_db
class TestCostTotals:
    def test_groups_active_subscriptions_by_currency(self, godaddy, digitalocean):
        make_subscription(godaddy, cost=Decimal("120.00"), currency="USD")
        make_subscription(
            digitalocean,
            cost=Decimal("6.00"),
            currency="USD",
            billing_cycle=Subscription.BillingCycle.MONTHLY,
        )
        make_subscription(
            godaddy,
            cost=Decimal("100.00"),
            currency="DKK",
            billing_cycle=Subscription.BillingCycle.TRIENNIAL,
        )
        make_subscription(godaddy, cost=Decimal("999.00"), currency="USD", active=False)

        totals = cost_totals()

        assert [(t.currency, t.subscriptions, t.monthly, t.yearly) for t in totals] == [
            ("DKK", 1, Decimal("2.78"), Decimal("33.33")),
            ("USD", 2, Decimal("16.00"), Decimal("192.00")),
        ]


@pytest.mark.django_db
class TestDashboardView:
    def test_requires_login(self, client):
        response = client.get(reverse("dashboard"))

        assert response.status_code == 302
        assert response.url == f"{reverse('login')}?next=/"

    def test_login_page_renders(self, client):
        response = client.get(reverse("login"))

        assert response.status_code == 200
        assert b"Log in" in response.content

    def test_empty_inventory_renders_empty_states(self, user_client):
        response = user_client.get(reverse("dashboard"))

        assert response.status_code == 200
        content = response.content.decode()
        assert "Nothing renews or expires in the next 60 days." in content
        assert "No active subscriptions yet." in content
        assert "No servers yet." in content
        assert "No apps yet." in content

    def test_shows_inventory(self, user_client, godaddy, cloudflare, digitalocean):
        hostinger = Provider.objects.create(name="Hostinger")
        Domain.objects.create(
            name="lapsed.example",
            registrar=godaddy,
            dns_provider=cloudflare,
            expires_on=days_from_today(-2),
        )
        Domain.objects.create(
            name="fine.example", registrar=godaddy, expires_on=days_from_today(300)
        )
        make_subscription(
            digitalocean,
            name="Droplet",
            cost=Decimal("6.00"),
            billing_cycle=Subscription.BillingCycle.MONTHLY,
            next_renewal=days_from_today(10),
        )
        make_subscription(hostinger, name="Web hosting", cost=Decimal("36.00"))
        home = Server.objects.create(name="home", kind=Server.Kind.HOME, host="203.0.113.10")
        Server.objects.create(name="droplet", provider=digitalocean, kind=Server.Kind.VPS)
        App.objects.create(
            name="grafana",
            server=home,
            url="https://grafana.example",
            last_checked_at=timezone.now(),
            last_check_ok=True,
            last_check_status_code=200,
            last_check_response_ms=42,
        )
        App.objects.create(
            name="blog",
            server=home,
            url="https://blog.example",
            last_checked_at=timezone.now(),
            last_check_ok=False,
            last_check_error="HTTP 502",
        )
        App.objects.create(name="old-site", server=home, status=App.Status.RETIRED)

        response = user_client.get(reverse("dashboard"))

        assert response.status_code == 200
        context = response.context
        assert context["counts"] == {
            "providers": 4,
            "domains": 2,
            "subscriptions": 2,
            "servers": 2,
            "apps": 2,
        }
        assert context["apps_down"] == 1
        assert context["overdue_count"] == 1
        assert [r.name for r in context["renewals"]] == ["lapsed.example", "Droplet"]
        assert [(t.currency, t.monthly, t.yearly) for t in context["cost_totals"]] == [
            ("USD", Decimal("9.00"), Decimal("108.00")),
        ]
        assert [(s.name, s.app_count) for s in context["servers"]] == [("droplet", 0), ("home", 2)]
        assert [a.name for a in context["apps"]] == ["blog", "grafana"]

        content = response.content.decode()
        assert 'class="row-overdue"' in content
        assert "2 days overdue" in content
        assert "1 overdue" in content
        assert "1 down" in content
        assert "200 · 42 ms" in content
        assert "HTTP 502" in content
        assert "203.0.113.10" in content
        assert "old-site" not in content

    def test_renewal_window_follows_setting(self, settings, user_client, godaddy):
        settings.HOMEBASE_RENEWAL_WINDOW_DAYS = 90
        Domain.objects.create(name="far.example", registrar=godaddy, expires_on=days_from_today(80))

        response = user_client.get(reverse("dashboard"))

        assert [r.name for r in response.context["renewals"]] == ["far.example"]
        assert "Next 90 days" in response.content.decode()

    def test_url_change_shows_not_checked_instead_of_the_old_result(self, user_client):
        app = App.objects.create(
            name="site",
            url="https://old.example",
            last_checked_at=timezone.now(),
            last_check_ok=True,
            last_check_status_code=200,
            last_check_response_ms=42,
        )
        app.url = "https://new.example"
        app.save()

        response = user_client.get(reverse("dashboard"))

        assert response.context["apps_down"] == 0
        content = response.content.decode()
        assert "Not checked" in content
        assert "https://new.example" in content
        assert "200 · 42 ms" not in content
        assert "badge-ok" not in content

    def test_clearing_url_shows_no_url_instead_of_the_old_result(self, user_client):
        app = App.objects.create(
            name="site",
            url="https://old.example",
            last_checked_at=timezone.now(),
            last_check_ok=False,
            last_check_error="Connection refused",
        )
        app.url = ""
        app.save()

        response = user_client.get(reverse("dashboard"))

        assert response.context["apps_down"] == 0
        content = response.content.decode()
        assert "No URL" in content
        assert "Connection refused" not in content
        assert "badge-bad" not in content

    def test_blank_url_is_not_down_when_a_stale_check_is_stored(self, user_client):
        App.objects.create(
            name="orphan",
            url="",
            last_checked_at=timezone.now(),
            last_check_ok=False,
            last_check_error="HTTP 500",
        )
        App.objects.create(
            name="real",
            url="https://real.example",
            last_checked_at=timezone.now(),
            last_check_ok=False,
            last_check_error="HTTP 502",
        )

        response = user_client.get(reverse("dashboard"))

        assert response.context["apps_down"] == 1
        content = response.content.decode()
        assert "No URL" in content
        assert "HTTP 500" not in content
        assert "HTTP 502" in content
        assert "1 down" in content


@pytest.mark.django_db
class TestLiveDashboard:
    @pytest.fixture(autouse=True)
    def no_tokens(self, settings):
        for name in ("CLOUDFLARE", "GODADDY", "DIGITALOCEAN", "HOSTINGER"):
            setattr(settings, f"{name}_API_TOKEN", "")
        settings.HOMEBASE_ALERT_EMAIL_ENABLED = False

    def test_alerts_card_lists_current_alerts_with_their_age(self, user_client, godaddy):
        Domain.objects.create(name="soon.example", registrar=godaddy, expires_on=days_from_today(3))
        update_alerts(timezone.now() - timedelta(hours=2))

        response = user_client.get(reverse("dashboard"))

        content = response.content.decode()
        assert response.context["critical_count"] == 1
        assert "soon.example expires in 3 days" in content
        assert "since 2\xa0hours ago" in content
        assert "Email is off" in content

    def test_no_current_alerts_without_alerts(self, user_client, settings):
        settings.DIGITALOCEAN_API_TOKEN = "do"
        Provider.objects.create(name="DigitalOcean", integration="digitalocean")
        now = timezone.now()
        App.objects.create(
            name="down",
            url="https://down.example",
            last_checked_at=now,
            last_check_ok=False,
            down_since=now,
        )
        content = user_client.get(reverse("dashboard")).content.decode()

        assert "No current alerts." in content

    def test_alerts_reflect_edits_before_the_next_monitor_run(self, user_client, godaddy):
        domain = Domain.objects.create(
            name="soon.example", registrar=godaddy, expires_on=days_from_today(3)
        )
        update_alerts()
        domain.expires_on = days_from_today(400)
        domain.save()

        content = user_client.get(reverse("dashboard")).content.decode()

        assert "soon.example expires" not in content
        assert "No current alerts." in content

    def test_provider_sync_states(self, settings, user_client):
        settings.CLOUDFLARE_API_TOKEN = "cf"
        settings.DIGITALOCEAN_API_TOKEN = "do"
        settings.GODADDY_API_TOKEN = "gd"
        now = timezone.now()
        Provider.objects.create(
            name="Cloudflare",
            integration="cloudflare",
            sync_attempted_at=now,
            sync_succeeded_at=now - timedelta(hours=5),
            sync_error="HTTP 403 from zones: Authentication error",
        )
        Provider.objects.create(
            name="DigitalOcean",
            integration="digitalocean",
            sync_attempted_at=now,
            sync_succeeded_at=now,
            sync_summary="2 droplets, 18.00 USD a month",
        )

        response = user_client.get(reverse("dashboard"))

        states = {row.label: row.state for row in response.context["providers"]}
        assert states == {
            "Cloudflare": "failing",
            "DigitalOcean": "ok",
            "GoDaddy": "pending",
            "Hostinger": "off",
        }
        content = response.content.decode()
        assert "HTTP 403 from zones: Authentication error" in content
        assert "2 droplets, 18.00 USD a month" in content
        assert "Set <code>HOSTINGER_API_TOKEN</code> to turn it on" in content

    def test_stalled_monitor_banner(self, settings, user_client):
        settings.HOMEBASE_CHECK_INTERVAL_SECONDS = 300
        app = App.objects.create(name="site", url="https://site.example")
        AppCheck.objects.create(
            app=app, url=app.url, checked_at=timezone.now() - timedelta(minutes=30), ok=True
        )

        content = user_client.get(reverse("dashboard")).content.decode()

        assert "The monitor has not run since" in content

    def test_fresh_monitor_has_no_banner(self, user_client):
        app = App.objects.create(name="site", url="https://site.example")
        AppCheck.objects.create(app=app, url=app.url, checked_at=timezone.now(), ok=True)

        content = user_client.get(reverse("dashboard")).content.decode()

        assert "The monitor has not run" not in content
        assert "No uptime check has run yet" not in content

    def test_uptime_columns(self, user_client):
        now = timezone.now()
        app = App.objects.create(
            name="site",
            url="https://site.example",
            last_checked_at=now,
            last_check_ok=False,
            last_check_error="HTTP 502",
            down_since=now - timedelta(minutes=20),
            last_check_tls_expires_at=now + timedelta(days=5, hours=1),
        )
        for minutes, ok in [(0, False), (5, True), (10, True), (15, True)]:
            AppCheck.objects.create(
                app=app, url=app.url, checked_at=now - timedelta(minutes=minutes), ok=ok
            )

        response = user_client.get(reverse("dashboard"))

        [row] = response.context["apps"]
        assert row.uptime_day == 75
        assert len(row.strip) == 24
        content = response.content.decode()
        assert "for 20 min</span>" in content
        assert '<span class="cell-sub text-bad-soft small">HTTP 502</span>' in content
        assert '<span class="badge badge-warn"' in content
        assert "5 d</span>" in content
        assert "75%" in content
        assert reverse("app_detail", args=[app.pk]) in content

    def test_domains_servers_and_tunnels(self, user_client, godaddy, cloudflare, digitalocean):
        domain = Domain.objects.create(
            name="example.com",
            registrar=godaddy,
            dns_provider=cloudflare,
            expires_on=days_from_today(300),
            registrar_status="ACTIVE",
            dns_zone_status="active",
        )
        DnsRecord.objects.create(
            domain=domain,
            external_id="r1",
            record_type="A",
            name="example.com",
            content="203.0.113.10",
            ttl=1,
        )
        Domain.objects.create(
            name="gone.example",
            registrar=godaddy,
            expires_on=days_from_today(300),
            registrar_status="missing",
        )
        server = Server.objects.create(
            name="web-1", provider=digitalocean, kind=Server.Kind.VPS, live_status="off"
        )
        make_subscription(
            digitalocean,
            server=server,
            cost=Decimal("6"),
            billing_cycle=Subscription.BillingCycle.MONTHLY,
        )
        Tunnel.objects.create(provider=cloudflare, external_id="t1", name="home", status="down")

        content = user_client.get(reverse("dashboard")).content.decode()

        assert f"?domain__id__exact={domain.pk}" in content
        assert '<span class="badge badge-bad">Missing</span>' in content
        assert ">Active</span>" not in content  # healthy registrar and zone states stay quiet
        assert '<span class="badge ">Off</span>' in content
        assert "6.00 USD" in content
        assert "Cloudflare Tunnels" in content
        assert '<span class="badge badge-bad">Down</span>' in content


@pytest.mark.django_db
class TestAppDetail:
    @pytest.mark.parametrize(
        ("now", "hours"),
        [
            (datetime(2026, 3, 29, 12, tzinfo=UTC), [0, 1, *range(3, 24)]),
            (datetime(2026, 10, 25, 12, tzinfo=UTC), [0, 1, 2, *range(2, 24)]),
        ],
    )
    def test_transition_day_grid_labels_match_cells(
        self, settings, user_client, monkeypatch, now, hours
    ):
        settings.TIME_ZONE = "Europe/Copenhagen"
        monkeypatch.setattr("inventory.views.timezone", SimpleNamespace(now=lambda: now))
        app = App.objects.create(name="site", url="https://site.example")

        response = user_client.get(reverse("app_detail", args=[app.pk]))

        class HourLabels(HTMLParser):
            def __init__(self):
                super().__init__()
                self.labels = []
                self.in_label = False

            def handle_starttag(self, tag, attrs):
                if tag == "span" and dict(attrs).get("class") == "cell cell-label":
                    self.labels.append("")
                    self.in_label = True

            def handle_data(self, data):
                if self.in_label:
                    self.labels[-1] += data

            def handle_endtag(self, tag):
                if tag == "span":
                    self.in_label = False

        rendered = HourLabels()
        rendered.feed(response.content.decode())
        assert len(rendered.labels) == 6 * 24 + len(hours)
        assert rendered.labels[-len(hours) :] == [
            f"{hour:02d}:00" if hour % 6 == 0 else "" for hour in hours
        ]
        assert len(response.context["days"][-1][1]) == len(hours)

    def test_returning_to_url_hides_checks_from_previous_period(self, user_client):
        app = App.objects.create(name="site", url="https://site.example")
        old_check = AppCheck.objects.create(
            app=app, url=app.url, checked_at=timezone.now(), ok=False, error="old failure"
        )
        app.url = "https://other.example"
        app.save()
        app.url = old_check.url
        app.save(update_fields=["url"])

        response = user_client.get(reverse("app_detail", args=[app.pk]))

        assert list(response.context["checks"]) == []
        assert response.context["uptime"] == {1: None, 7: None, 30: None}
        assert response.context["outages"] == []
        assert "old failure" not in response.content.decode()
        assert app.checks.count() == 1

        new_check = AppCheck.objects.create(
            app=app, url=app.url, checked_at=timezone.now(), ok=True, response_ms=80
        )
        response = user_client.get(reverse("app_detail", args=[app.pk]))
        assert list(response.context["checks"]) == [new_check]
        assert response.context["uptime"] == {1: 100, 7: 100, 30: 100}
        assert response.context["average_ms"] == 80

    def test_requires_login(self, client):
        app = App.objects.create(name="site", url="https://site.example")

        response = client.get(reverse("app_detail", args=[app.pk]))

        assert response.status_code == 302

    def test_unknown_app_is_404(self, user_client):
        assert user_client.get(reverse("app_detail", args=[999])).status_code == 404

    def test_shows_history_outages_and_checks(self, user_client):
        now = timezone.now()
        app = App.objects.create(
            name="site",
            url="https://site.example",
            last_checked_at=now,
            last_check_ok=True,
            last_check_status_code=200,
            last_check_response_ms=80,
            last_check_tls_expires_at=now + timedelta(days=40, hours=1),
        )
        sequence = [(25, True), (20, False), (15, False), (10, True), (5, True)]
        for minutes, ok in sequence:
            AppCheck.objects.create(
                app=app,
                url=app.url,
                checked_at=now - timedelta(minutes=minutes),
                ok=ok,
                status_code=200 if ok else 503,
                response_ms=80 if ok else None,
                error="" if ok else "HTTP 503",
            )
        AppCheck.objects.create(
            app=app, url="https://old.example", checked_at=now, ok=False, error="old url"
        )

        response = user_client.get(reverse("app_detail", args=[app.pk]))

        assert response.status_code == 200
        assert response.context["uptime"][1] == 60
        assert [(o.checks, o.error) for o, _ in response.context["outages"]] == [(2, "HTTP 503")]
        assert len(response.context["days"]) == 7
        content = response.content.decode()
        assert "60%" in content
        assert "10 min" in content
        assert "old url" not in content
        assert "80 ms" in content
        assert "40 d" in content

    def test_app_without_url(self, user_client):
        app = App.objects.create(name="notes")

        content = user_client.get(reverse("app_detail", args=[app.pk])).content.decode()

        assert "has no URL" in content
