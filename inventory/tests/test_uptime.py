import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO

import pytest
import trustme
from django.core.management import call_command
from django.utils import timezone

from inventory import uptime
from inventory.models import App, AppCheck
from inventory.monitoring import check_app, probe, prune_history

CERT_EXPIRES = datetime(2030, 6, 1, 12, 0, tzinfo=UTC)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(500 if self.path == "/broken" else 200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # The expiry check closes its TLS connection without sending a request.
        pass


@pytest.fixture(scope="module")
def ca():
    return trustme.CA()


@pytest.fixture(scope="module")
def https_server(ca):
    cert = ca.issue_cert("localhost", not_after=CERT_EXPIRES)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    cert.configure_cert(context)
    server = QuietServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://localhost:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def trusting(ca):
    context = ssl.create_default_context()
    ca.configure_trust(context)
    return context


class TestTlsExpiry:
    def test_reads_the_certificate_expiry(self, https_server, trusting):
        result = probe(f"{https_server}/", timeout=5, ssl_context=trusting)

        assert result.ok
        assert result.tls_expires_at == CERT_EXPIRES

    def test_reads_expiry_even_when_the_app_answers_with_an_error(self, https_server, trusting):
        result = probe(f"{https_server}/broken", timeout=5, ssl_context=trusting)

        assert (result.ok, result.status_code) == (False, 500)
        assert result.tls_expires_at == CERT_EXPIRES

    def test_untrusted_certificate_is_down_without_an_expiry(self, https_server):
        result = probe(f"{https_server}/", timeout=5)

        assert not result.ok
        assert result.error.startswith("TLS certificate invalid")
        assert result.tls_expires_at is None


def make_app(url="https://app.example", **fields):
    return App.objects.create(name=fields.pop("name", "site"), url=url, **fields)


@pytest.mark.django_db
class TestCheckApp:
    def test_records_history_and_tracks_downtime(self, https_server, trusting, monkeypatch):
        app = make_app(f"{https_server}/")
        outcomes = iter(["/", "/broken", "/broken", "/"])
        real_probe = probe

        def scripted(url, timeout, ssl_context=None):
            return real_probe(f"{https_server}{next(outcomes)}", timeout, ssl_context)

        monkeypatch.setattr("inventory.monitoring.probe", scripted)

        states = []
        for _ in range(4):
            check_app(app, timeout=5, ssl_context=trusting)
            app.refresh_from_db()
            states.append((app.last_check_ok, app.down_since))

        assert [ok for ok, _ in states] == [True, False, False, True]
        assert states[0][1] is None
        assert states[1][1] is not None
        assert states[2][1] == states[1][1]
        assert states[3][1] is None
        checks = list(app.checks.order_by("checked_at"))
        assert [c.ok for c in checks] == [True, False, False, True]
        assert {c.url for c in checks} == {f"{https_server}/"}
        assert checks[1].status_code == 500
        assert app.last_check_tls_expires_at == CERT_EXPIRES
        assert states[1][1] == checks[1].checked_at

    def test_keeps_the_last_known_tls_expiry_when_the_handshake_fails(self, https_server, trusting):
        app = make_app(f"{https_server}/")
        check_app(app, timeout=5, ssl_context=trusting)

        check_app(app, timeout=5)  # Without the test CA the certificate does not verify.

        app.refresh_from_db()
        assert app.last_check_ok is False
        assert app.last_check_tls_expires_at == CERT_EXPIRES

    def test_url_change_clears_tls_and_downtime(self):
        app = make_app(
            last_check_tls_expires_at=CERT_EXPIRES,
            down_since=timezone.now(),
            last_check_ok=False,
        )

        app.url = "https://other.example"
        app.save()
        app.refresh_from_db()

        assert (app.last_check_tls_expires_at, app.down_since) == (None, None)

    def test_command_prints_tls_days(self, https_server, monkeypatch, trusting):
        make_app(f"{https_server}/")
        real_probe = probe
        monkeypatch.setattr(
            "inventory.monitoring.probe",
            lambda url, timeout, ssl_context=None: real_probe(url, timeout, trusting),
        )
        out = StringIO()

        call_command("check_apps", stdout=out)

        days = (CERT_EXPIRES - timezone.now()).days
        assert f"TLS expires in {days} d" in out.getvalue()


def add_check(app, at, ok=True, url=None, error=""):
    return AppCheck.objects.create(
        app=app,
        url=url or app.url,
        checked_at=at,
        ok=ok,
        status_code=200 if ok else None,
        response_ms=100 if ok else None,
        error=error,
    )


@pytest.mark.django_db
class TestUptimeHistory:
    NOW = datetime(2026, 10, 5, 12, 30, tzinfo=UTC)

    @pytest.mark.parametrize("intermediate_url", ["https://other.example", ""])
    @pytest.mark.parametrize("update_fields", [None, ["url"]])
    def test_returning_to_url_starts_a_fresh_period(
        self, monkeypatch, intermediate_url, update_fields
    ):
        app = make_app()
        original_url = app.url
        add_check(app, self.NOW - timedelta(minutes=10), ok=False)
        add_check(app, self.NOW - timedelta(minutes=5))
        first_change = self.NOW - timedelta(minutes=1)
        monkeypatch.setattr("inventory.models.timezone.now", lambda: first_change)

        app.url = intermediate_url
        app.save(update_fields=update_fields)
        app.refresh_from_db()
        assert app.monitoring_since == first_change
        monkeypatch.setattr("inventory.models.timezone.now", lambda: self.NOW)
        app.url = original_url
        app.save(update_fields=update_fields)
        app.refresh_from_db()

        since = self.NOW - timedelta(days=1)
        assert app.last_check_ok is None
        assert app.monitoring_since == self.NOW
        assert all(b.total == 0 for b in uptime.last_day_buckets([app.pk], self.NOW)[app.pk])
        assert uptime.uptime_percent([app.pk], since)[app.pk] is None
        assert uptime.average_response_ms(app.pk, since) is None
        assert uptime.outages(app.pk, since) == []
        assert not uptime.current_checks([app.pk]).exists()
        assert app.checks.count() == 2

        check = add_check(app, self.NOW)
        assert list(uptime.current_checks([app.pk])) == [check]
        assert uptime.last_day_buckets([app.pk], self.NOW)[app.pk][-1].total == 1
        assert uptime.uptime_percent([app.pk], since)[app.pk] == 100
        assert uptime.average_response_ms(app.pk, since) == 100
        assert uptime.outages(app.pk, since) == []

    def test_save_omitting_url_preserves_the_monitoring_period(self):
        app = make_app()
        app.url = "https://other.example"
        app.save(update_fields=["notes"])
        app.refresh_from_db()

        assert app.url == "https://app.example"
        assert app.monitoring_since is None

    def test_last_day_buckets_by_hour(self):
        app = make_app()
        add_check(app, self.NOW - timedelta(minutes=5))
        add_check(app, self.NOW - timedelta(minutes=20), ok=False)
        add_check(app, self.NOW - timedelta(hours=1, minutes=10), ok=False)
        add_check(app, self.NOW - timedelta(hours=2, minutes=10))
        add_check(app, self.NOW - timedelta(minutes=5), url="https://old.example", ok=False)

        buckets = uptime.last_day_buckets([app.pk], self.NOW)[app.pk]

        assert len(buckets) == 24
        assert buckets[-1].start == datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        assert [b.state for b in buckets[-3:]] == ["up", "down", "partial"]
        assert buckets[-1].label.endswith("1 of 2 checks failed")
        assert buckets[0].state == "none"

    def test_uptime_percent_counts_only_the_current_url(self):
        app = make_app()
        other = make_app(name="other", url="https://other.example")
        for minutes in range(0, 40, 5):
            add_check(app, self.NOW - timedelta(minutes=minutes), ok=minutes != 10)
        add_check(app, self.NOW - timedelta(minutes=1), url="https://old.example", ok=False)
        add_check(app, self.NOW - timedelta(days=3), ok=False)

        result = uptime.uptime_percent([app.pk, other.pk], self.NOW - timedelta(days=1))

        assert result == {app.pk: 87.5, other.pk: None}

    def test_outages_group_consecutive_failures(self):
        app = make_app()
        sequence = [True, False, False, True, True, False]
        for index, ok in enumerate(sequence):
            add_check(
                app,
                self.NOW - timedelta(minutes=5 * (len(sequence) - index)),
                ok=ok,
                error="" if ok else f"e{index}",
            )

        found = uptime.outages(app.pk, self.NOW - timedelta(days=1))

        assert [(o.checks, o.error, o.ended_at is None) for o in found] == [
            (1, "e5", True),
            (2, "e1", False),
        ]
        assert found[1].duration(self.NOW) == timedelta(minutes=10)

    def test_daily_rows_cover_local_calendar_days(self, settings):
        settings.TIME_ZONE = "Europe/Copenhagen"
        app = make_app()

        rows = uptime.daily_rows(app.pk, self.NOW, days=7)

        assert [day.isoformat() for day, _ in rows][-1] == "2026-10-05"
        assert len(rows) == 7
        assert all(len(buckets) == 24 for _, buckets in rows)
        today = rows[-1][1]
        assert timezone.localtime(today[0].start).hour == 0
        assert [b.future for b in today].index(True) == 15  # 14:30 local now, 15:00 is next

    @pytest.mark.parametrize(
        ("now", "hours", "interval"),
        [
            (datetime(2026, 3, 29, 12, tzinfo=UTC), [0, 1, *range(3, 24)], "01:00-03:00"),
            (datetime(2026, 10, 25, 12, tzinfo=UTC), [0, 1, 2, *range(2, 24)], "02:00-02:00"),
        ],
    )
    def test_transition_days_use_absolute_hour_intervals(self, settings, now, hours, interval):
        settings.TIME_ZONE = "Europe/Copenhagen"
        app = make_app()

        buckets = uptime.daily_rows(app.pk, now, days=1)[0][1]

        assert [timezone.localtime(b.start).hour for b in buckets] == hours
        assert interval in buckets[1 if len(hours) == 23 else 2].label
        assert all(b.start.tzinfo == UTC for b in buckets)

    def test_prune_history(self):
        app = make_app()
        add_check(app, self.NOW - timedelta(days=91))
        kept = add_check(app, self.NOW - timedelta(days=89))

        assert prune_history(self.NOW, keep_days=90) == 1
        assert list(AppCheck.objects.all()) == [kept]
