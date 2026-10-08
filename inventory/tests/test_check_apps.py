import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO

import pytest
from django.core.management import call_command

from inventory.models import App
from inventory.monitoring import probe


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
            return
        status = {"/ok": 200, "/redirect": 302, "/broken": 500}.get(self.path, 404)
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/ok")
        self.end_headers()

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def closed_port_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/"


class TestProbe:
    def test_success(self, http_server):
        result = probe(f"{http_server}/ok", timeout=5)

        assert result.ok
        assert result.status_code == 200
        assert result.response_ms is not None
        assert result.error == ""

    def test_follows_redirects(self, http_server):
        result = probe(f"{http_server}/redirect", timeout=5)

        assert result.ok
        assert result.status_code == 200

    def test_redirect_loop_is_down(self, http_server):
        result = probe(f"{http_server}/loop", timeout=5)

        assert result.ok is False
        assert result.status_code == 302
        assert result.error == "Redirect not completed (HTTP 302)"

    def test_http_error_status_is_down(self, http_server):
        result = probe(f"{http_server}/broken", timeout=5)

        assert not result.ok
        assert result.status_code == 500
        assert result.error == "HTTP 500"

    def test_unreachable_host_is_down(self, closed_port_url):
        result = probe(closed_port_url, timeout=5)

        assert not result.ok
        assert result.status_code is None
        assert result.error == "Connection refused"

    def test_unknown_host_is_down(self):
        result = probe("http://homebase-test.invalid/", timeout=5)

        assert not result.ok
        assert result.error == "DNS lookup failed"

    def test_timeout_is_down(self):
        with socket.create_server(("127.0.0.1", 0)) as silent:
            port = silent.getsockname()[1]
            result = probe(f"http://127.0.0.1:{port}/", timeout=0.2)

        assert not result.ok
        assert result.error == "Timed out"

    @pytest.mark.parametrize("status", [None, "200"])
    def test_non_int_status_is_a_failed_check(self, monkeypatch, status):
        monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: _Response(status))

        result = probe("http://example.test/file", timeout=5)

        assert result.ok is False
        assert result.status_code is None
        assert result.error == "Not an HTTP response"


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


@pytest.mark.django_db
def test_check_apps_command_records_results(http_server, closed_port_url):
    up = App.objects.create(name="up", url=f"{http_server}/ok")
    broken = App.objects.create(name="broken", url=f"{http_server}/broken")
    gone = App.objects.create(name="gone", url=closed_port_url)
    retired = App.objects.create(name="retired", url=f"{http_server}/ok", status=App.Status.RETIRED)
    no_url = App.objects.create(name="no-url")
    out = StringIO()

    call_command("check_apps", "--timeout", "5", stdout=out)

    for app in (up, broken, gone, retired, no_url):
        app.refresh_from_db()
    assert (up.last_check_ok, up.last_check_status_code, up.last_check_error) == (True, 200, "")
    assert up.last_checked_at is not None
    assert (broken.last_check_ok, broken.last_check_status_code) == (False, 500)
    assert gone.last_check_ok is False
    assert gone.last_check_error
    assert retired.last_checked_at is None
    assert no_url.last_checked_at is None
    assert "Checked 3 app(s), 2 down." in out.getvalue()


@pytest.mark.django_db
def test_non_http_response_does_not_stop_later_apps(http_server, monkeypatch):
    real_urlopen = urllib.request.urlopen

    def urlopen(request, timeout=None, **kwargs):
        if request.full_url.endswith("/ftp-like"):
            return _Response(None)
        return real_urlopen(request, timeout=timeout, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    weird = App.objects.create(name="aaa-weird", url=f"{http_server}/ftp-like")
    later = App.objects.create(name="zzz-later", url=f"{http_server}/ok")
    out = StringIO()

    call_command("check_apps", "--timeout", "5", stdout=out)

    weird.refresh_from_db()
    later.refresh_from_db()
    assert weird.last_check_ok is False
    assert weird.last_check_status_code is None
    assert weird.last_check_error == "Not an HTTP response"
    assert later.last_check_ok is True
    assert "Checked 2 app(s), 1 down." in out.getvalue()


@pytest.mark.django_db
def test_check_apps_continues_when_one_probe_raises(http_server, monkeypatch):
    real_probe = probe

    def flaky(url, timeout, ssl_context=None):
        if url.endswith("/explode"):
            raise TypeError("status")
        return real_probe(url, timeout, ssl_context)

    monkeypatch.setattr("inventory.monitoring.probe", flaky)
    App.objects.create(name="aaa-explode", url=f"{http_server}/explode")
    later = App.objects.create(name="zzz-later", url=f"{http_server}/ok")
    out = StringIO()

    call_command("check_apps", "--timeout", "5", stdout=out)

    later.refresh_from_db()
    assert later.last_check_ok is True
    text = out.getvalue()
    assert "aaa-explode" in text
    assert "Checked 2 app(s), 1 down." in text


@pytest.mark.django_db
def test_url_change_between_snapshot_and_write_is_not_current(http_server, monkeypatch):
    app = App.objects.create(name="site", url=f"{http_server}/ok")
    later = App.objects.create(name="zzz-later", url=f"{http_server}/ok")
    real_probe = probe

    def change_url_then_probe(url, timeout, ssl_context=None):
        if url == app.url:
            stored = App.objects.get(pk=app.pk)
            stored.url = "https://new.example"
            stored.save()
        return real_probe(url, timeout, ssl_context)

    monkeypatch.setattr("inventory.monitoring.probe", change_url_then_probe)
    out = StringIO()

    call_command("check_apps", "--timeout", "5", stdout=out)

    app.refresh_from_db()
    later.refresh_from_db()
    assert app.url == "https://new.example"
    assert app.last_checked_at is None
    assert app.last_check_ok is None
    assert app.last_check_status_code is None
    assert app.last_check_response_ms is None
    assert app.last_check_error == ""
    assert later.last_check_ok is True
    text = out.getvalue()
    assert "up    site" not in text
    assert "down  site" not in text
    assert "up    zzz-later" in text
    assert "Checked 2 app(s), 0 down." in text


@pytest.mark.django_db
@pytest.mark.parametrize("existing_period", [False, True])
def test_probe_across_url_round_trip_is_discarded(http_server, monkeypatch, existing_period):
    url = f"{http_server}/ok"
    app = App.objects.create(name="site", url="https://earlier.example" if existing_period else url)
    if existing_period:
        app.url = url
        app.save()
    real_probe = probe

    def change_period_then_probe(url, timeout, ssl_context=None):
        stored = App.objects.get(pk=app.pk)
        stored.url = "https://other.example"
        stored.save()
        stored.url = url
        stored.save(update_fields=["url"])
        return real_probe(url, timeout, ssl_context)

    monkeypatch.setattr("inventory.monitoring.probe", change_period_then_probe)
    out = StringIO()

    call_command("check_apps", "--timeout", "5", stdout=out)

    app.refresh_from_db()
    assert app.url == url
    assert all(getattr(app, field) == cleared for field, cleared in App.CHECK_STATE.items())
    assert not app.checks.exists()
    assert "up    site" not in out.getvalue()
    assert "down  site" not in out.getvalue()

    monkeypatch.setattr("inventory.monitoring.probe", real_probe)
    call_command("check_apps", "--timeout", "5", stdout=StringIO())
    app.refresh_from_db()
    assert app.last_check_ok is True
    assert app.checks.count() == 1
