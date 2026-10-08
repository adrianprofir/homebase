import os
import socket
import subprocess
import sys
import urllib.request
from datetime import timedelta
from io import StringIO
from pathlib import Path

import httpx
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from inventory.alerts import current_alerts
from inventory.dashboard import provider_statuses, upcoming_renewals
from inventory.demo import lab, refuse_unsafe_settings
from inventory.demo.checks import simulated_probe
from inventory.models import Alert, App, AppCheck, Domain, Provider
from inventory.uptime import outages

pytestmark = pytest.mark.django_db

BASE_DIR = Path(__file__).resolve().parents[2]
TOKENS = [
    "CLOUDFLARE_API_TOKEN",
    "GODADDY_API_TOKEN",
    "DIGITALOCEAN_API_TOKEN",
    "HOSTINGER_API_TOKEN",
]


@pytest.fixture(autouse=True)
def _short_history(settings):
    # Fewer simulated checks keep the seed quick; the history test sets the real interval.
    settings.HOMEBASE_CHECK_INTERVAL_SECONDS = 1200


@pytest.fixture
def demo(settings):
    settings.DEMO_MODE = True
    for name in TOKENS:
        setattr(settings, name, "")
    return settings


def seed(*args):
    out = StringIO()
    call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


def manage(*args, **env):
    """Run manage.py in a fresh process, so settings are read from `env` at startup."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", *args],
        cwd=BASE_DIR,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestStartupRefusal:
    @pytest.mark.parametrize("name", TOKENS)
    def test_refuses_any_provider_token(self, demo, name):
        setattr(demo, name, "a-real-token")

        with pytest.raises(ImproperlyConfigured, match=name):
            refuse_unsafe_settings()

    def test_refuses_smtp(self, demo):
        demo.MAILERS = {
            "default": {
                "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
                "OPTIONS": {"host": "smtp.example.com"},
            }
        }

        with pytest.raises(ImproperlyConfigured, match="EMAIL_HOST"):
            refuse_unsafe_settings()

    def test_allows_a_clean_demo_and_tokens_outside_demo_mode(self, demo):
        refuse_unsafe_settings()
        demo.DEMO_MODE = False
        demo.HOSTINGER_API_TOKEN = "a-real-token"
        refuse_unsafe_settings()

    def test_manage_py_will_not_start(self):
        result = manage("check", DEMO_MODE="true", GODADDY_API_TOKEN="a-real-token")

        assert result.returncode != 0
        assert "refuses to start while GODADDY_API_TOKEN is set" in result.stderr

    def test_https_settings_come_from_the_environment(self):
        result = manage(
            "shell",
            "-c",
            "from django.conf import settings as s; "
            "print(s.SECURE_PROXY_SSL_HEADER, s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS)",
            TRUST_X_FORWARDED_PROTO="true",
            SECURE_SSL_REDIRECT="true",
            SECURE_HSTS_SECONDS="3600",
        )

        assert result.returncode == 0, result.stderr
        assert "('HTTP_X_FORWARDED_PROTO', 'https') True 3600" in result.stdout


class TestHealthz:
    def test_answers_ok_for_any_host_and_without_https(self, client, settings):
        settings.ALLOWED_HOSTS = ["homebase.example"]
        settings.SECURE_SSL_REDIRECT = True

        response = client.get("/healthz", HTTP_HOST="127.0.0.1")

        assert response.status_code == 200
        assert response.content == b"ok\n"
        assert response["Cache-Control"] == "no-store"

    def test_reports_an_unreachable_database(self, client, monkeypatch):
        def broken():
            raise OSError("connection refused")

        monkeypatch.setattr(connection, "cursor", broken)

        response = client.get("/healthz")

        assert response.status_code == 503
        assert response.content == b"database unavailable\n"

    def test_https_redirect_still_applies_elsewhere(self, client, settings):
        settings.SECURE_SSL_REDIRECT = True

        response = client.get("/accounts/login/")

        assert response.status_code == 301
        assert response["Location"].startswith("https://")


class TestPublicPages:
    def test_login_is_required_outside_demo_mode(self, client, settings):
        settings.DEMO_MODE = False
        seed()
        app = App.objects.get(name="Wiki")

        for url in [reverse("dashboard"), reverse("app_detail", args=[app.pk])]:
            response = client.get(url)
            assert response.status_code == 302
            assert response["Location"].startswith(reverse("login"))

    def test_dashboard_and_app_pages_are_public_and_read_only(self, client, demo):
        seed()
        app = App.objects.get(name="Wiki")

        for url in [reverse("dashboard"), reverse("app_detail", args=[app.pk])]:
            response = client.get(url)
            assert response.status_code == 200
            assert "Everything here is fictional" in response.text
            # Anonymous demo pages start no session and set no CSRF cookie.
            assert not response.cookies
            assert client.post(url).status_code == 405

    def test_admin_links_render_as_text(self, client, demo):
        seed()
        page = client.get(reverse("dashboard")).text

        assert 'href="/admin/' not in page
        assert "fernhill-photos.example" in page
        assert '<span class="alert-title">' in page
        # Links within the dashboard stay links; links to fictional hosts do not.
        assert f'href="{reverse("app_detail", args=[App.objects.get(name="Wiki").pk])}"' in page
        assert 'href="https://wiki.quietbrook.example/"' not in page

    def test_outside_demo_mode_admin_links_stay(self, client, settings, django_user_model):
        settings.DEMO_MODE = False
        seed()
        client.force_login(django_user_model.objects.create_user("alice"))
        domain = Domain.objects.get(name="fernhill.example")

        page = client.get(reverse("dashboard")).text

        assert f'href="{reverse("admin:inventory_domain_change", args=[domain.pk])}"' in page
        assert f'?domain__id__exact={domain.pk}"' in page
        assert "Everything here is fictional" not in page

    @pytest.mark.parametrize(("url", "shown"), [("https://www.example.org", True), ("", False)])
    def test_main_site_strip(self, client, demo, url, shown):
        demo.MAIN_SITE_URL = url

        page = client.get(reverse("dashboard")).text

        assert ('href="https://www.example.org"' in page) is shown
        assert ("Back to www.example.org" in page) is shown


class TestSeedDemo:
    def test_builds_the_whole_story_relative_to_today(self, demo):
        output = seed()

        now = timezone.now()
        today = timezone.localdate(now)
        assert output.startswith("Seeded 5 providers, 5 domains, 4 servers")
        days = sorted(r.days_left for r in upcoming_renewals(today, 60))
        assert any(5 <= d <= 60 for d in days)
        lapsing = Domain.objects.get(name="fernhill-photos.example")
        assert not lapsing.auto_renew and 0 < (lapsing.expires_on - today).days <= 7
        alerts = {alert.kind: alert for alert in current_alerts(now)}
        assert set(alerts) == {
            Alert.Kind.DOMAIN_EXPIRY,
            Alert.Kind.SUBSCRIPTION_RENEWAL,
            Alert.Kind.TLS_EXPIRY,
            Alert.Kind.DOWNTIME,
            Alert.Kind.SYNC_FAILURE,
        }
        assert alerts[Alert.Kind.DOWNTIME].title.startswith("Wiki has been down for")
        assert "Password vault" in alerts[Alert.Kind.TLS_EXPIRY].title
        states = {row.label: row.state for row in provider_statuses(now)}
        assert states == {
            "Cloudflare": "ok",
            "DigitalOcean": "ok",
            "GoDaddy": "ok",
            "Hostinger": "failing",
        }
        hostinger = Provider.objects.get(integration="hostinger")
        assert hostinger.sync_error.startswith("HTTP 401")
        assert hostinger.sync_succeeded_at < now - timedelta(days=1)

    def test_writes_30_days_of_history_with_two_short_outages(self, demo):
        demo.HOMEBASE_CHECK_INTERVAL_SECONDS = 300
        seed()

        now = timezone.now()
        oldest = AppCheck.objects.order_by("checked_at").first().checked_at
        assert timedelta(days=29) < now - oldest <= timedelta(days=30, minutes=1)
        finished = [
            (app.name, outage)
            for app in App.checkable()
            for outage in outages(app.pk, now - timedelta(days=30))
            if outage.ended_at
        ]
        assert sorted(name for name, _ in finished) == ["Photo library", "Recipe box"]
        assert all(outage.duration(now) <= timedelta(hours=2) for _, outage in finished)
        wiki = App.objects.get(name="Wiki")
        assert wiki.last_check_ok is False
        assert timedelta(minutes=30) < now - wiki.down_since < timedelta(minutes=40)
        assert App.objects.get(name="Photo library").last_check_ok is True

    def test_refuses_an_inventory_that_has_data(self, demo, godaddy):
        with pytest.raises(CommandError, match="not empty"):
            seed()

        assert seed("--if-empty") == "The inventory already has data; not seeding.\n"
        assert Provider.objects.count() == 1

    def test_reset_needs_demo_mode(self, settings, godaddy):
        settings.DEMO_MODE = False

        with pytest.raises(CommandError, match="DEMO_MODE"):
            seed("--reset")

        assert Provider.objects.get() == godaddy

    def test_reset_rebuilds_the_inventory_and_keeps_users(self, demo, django_user_model):
        seed()
        user = django_user_model.objects.create_user("operator")
        Domain.objects.filter(name="fernhill.example").update(notes="changed by a visitor")
        checks = AppCheck.objects.count()

        seed("--reset")

        assert Domain.objects.get(name="fernhill.example").notes == ""
        assert AppCheck.objects.count() == checks
        assert django_user_model.objects.filter(pk=user.pk).exists()


class TestSimulatedChecks:
    def test_is_deterministic(self):
        at = timezone.now()

        assert simulated_probe("https://media.fernhill.example/", at) == simulated_probe(
            "https://media.fernhill.example/", at
        )

    def test_unknown_apps_are_up_and_down_apps_stay_down(self):
        at = timezone.now()

        unknown = simulated_probe("http://added-in-the-admin.example/", at)
        down = simulated_probe("https://wiki.quietbrook.example/", at)

        assert unknown.ok and unknown.status_code == 200 and unknown.tls_expires_at is None
        assert not down.ok and down.error == "HTTP 502"

    def test_tls_expiry_follows_today(self):
        vault = next(app for app in lab.APPS if app.name == "Password vault")
        at = timezone.now()

        result = simulated_probe(vault.url, at)

        assert (result.tls_expires_at - at).days in (vault.tls_days - 1, vault.tls_days)


class TestNoOutboundRequests:
    @pytest.fixture
    def network_calls(self, monkeypatch):
        """Record and block every way homebase could reach the network."""
        calls = []

        def blocked(name):
            def fail(*args, **kwargs):
                calls.append(name)
                raise AssertionError(f"demo mode made an outbound request via {name}")

            return fail

        monkeypatch.setattr(socket, "create_connection", blocked("socket.create_connection"))
        monkeypatch.setattr(socket, "getaddrinfo", blocked("socket.getaddrinfo"))
        monkeypatch.setattr(socket.socket, "connect", blocked("socket.connect"))
        monkeypatch.setattr(socket.socket, "connect_ex", blocked("socket.connect_ex"))
        monkeypatch.setattr(urllib.request, "urlopen", blocked("urllib.request.urlopen"))
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked("httpx"))
        return calls

    def test_demo_mode_makes_no_outbound_request(self, demo, client, network_calls):
        seed()
        seed("--reset")
        App.objects.create(name="Added in the admin", url="https://added.example/")

        call_command("monitor", stdout=StringIO())
        call_command("check_apps", stdout=StringIO())
        with pytest.raises(CommandError, match="1 provider sync"):
            call_command("sync_providers", stdout=StringIO())
        pages = [reverse("dashboard")] + [
            reverse("app_detail", args=[pk]) for pk in App.objects.values_list("pk", flat=True)
        ]
        for url in pages:
            assert client.get(url).status_code == 200

        assert network_calls == []
        # Everything above really ran: checks were stored and the sync was attempted.
        assert App.objects.get(name="Added in the admin").last_check_ok is True
        assert Provider.objects.filter(integration="hostinger").get().sync_error

    def test_the_seed_makes_no_outbound_request_outside_demo_mode(self, settings, network_calls):
        settings.DEMO_MODE = False

        seed()

        assert network_calls == []
