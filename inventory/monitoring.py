"""Reachability checks for apps: one HTTP GET per app URL, plus its TLS certificate expiry."""

import dataclasses
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from inventory.models import App, AppCheck

USER_AGENT = "homebase-reachability-check/1.0"


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    status_code: int | None
    response_ms: int | None
    error: str
    tls_expires_at: datetime | None = None


def probe(url, timeout, ssl_context=None):
    """GET `url` and, for https, read when its certificate expires.

    A completed response below 400 is up; any HTTPError is down.
    """
    result = probe_http(url, timeout, ssl_context)
    parts = urllib.parse.urlsplit(url)
    # Without an HTTP status the handshake failed or never happened, so there is no
    # verified certificate to read.
    if parts.scheme == "https" and result.status_code is not None:
        try:
            expires_at = tls_certificate_expiry(
                parts.hostname, parts.port or 443, timeout, ssl_context
            )
        except OSError, ValueError, KeyError:
            expires_at = None
        result = dataclasses.replace(result, tls_expires_at=expires_at)
    return result


def tls_certificate_expiry(host, port, timeout, ssl_context=None):
    """When the verified certificate that `host` serves on `port` expires."""
    context = ssl_context or ssl.create_default_context()
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        context.wrap_socket(sock, server_hostname=host) as tls,
    ):
        not_after = tls.getpeercert()["notAfter"]
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC)


def probe_http(url, timeout, ssl_context=None):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    started = time.monotonic()
    http_error = False
    try:
        with urllib.request.urlopen(  # noqa: S310
            request, timeout=timeout, context=ssl_context
        ) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        http_error = True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return CheckResult(ok=False, status_code=None, response_ms=None, error=describe_error(exc))
    elapsed_ms = round((time.monotonic() - started) * 1000)
    # Non-HTTP handlers (for example FTP) expose a non-int status and must fail
    # this check rather than raise.
    if not isinstance(status, int):
        return CheckResult(
            ok=False,
            status_code=None,
            response_ms=elapsed_ms,
            error="Not an HTTP response",
        )
    if http_error and 300 <= status < 400:
        return CheckResult(
            ok=False,
            status_code=status,
            response_ms=elapsed_ms,
            error=f"Redirect not completed (HTTP {status})",
        )
    ok = status < 400 and not http_error
    return CheckResult(
        ok=ok, status_code=status, response_ms=elapsed_ms, error="" if ok else f"HTTP {status}"
    )


def check_app(app, timeout, ssl_context=None):
    """Store the probe only if this URL is still current; otherwise return None.

    Each stored probe also lands in the app's uptime history. `down_since` holds the
    first failed check of the current outage. In demo mode the probe is simulated.
    """
    probed_url = app.url
    probed_since = app.monitoring_since
    if settings.DEMO_MODE:
        # Imported here because the simulator builds on CheckResult from this module.
        from inventory.demo.checks import simulated_probe

        result = simulated_probe(probed_url, timezone.now())
    else:
        result = probe(probed_url, timeout, ssl_context)
    checked_at = timezone.now()
    with transaction.atomic():
        current = (
            App.objects.select_for_update()
            .filter(pk=app.pk, url=probed_url, monitoring_since=probed_since)
            .values("down_since")
            .first()
        )
        if current is None:
            return None
        fields = {
            "last_checked_at": checked_at,
            "last_check_ok": result.ok,
            "last_check_status_code": result.status_code,
            "last_check_response_ms": result.response_ms,
            "last_check_error": result.error,
            "down_since": None if result.ok else current["down_since"] or checked_at,
        }
        # An expired or otherwise invalid certificate cannot be read, so the last
        # known expiry stays and keeps the TLS alert accurate.
        if result.tls_expires_at is not None:
            fields["last_check_tls_expires_at"] = result.tls_expires_at
        App.objects.filter(pk=app.pk).update(**fields)
        AppCheck.objects.create(
            app_id=app.pk,
            url=probed_url,
            checked_at=checked_at,
            ok=result.ok,
            status_code=result.status_code,
            response_ms=result.response_ms,
            error=result.error,
            tls_expires_at=result.tls_expires_at,
        )
    return result


def prune_history(now, keep_days):
    """Delete uptime history older than `keep_days`. Returns how many checks went."""
    deleted, _ = AppCheck.objects.filter(checked_at__lt=now - timedelta(days=keep_days)).delete()
    return deleted


def describe_error(exc):
    """A short, human-readable reason for a failed request."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        message = "DNS lookup failed"
    elif isinstance(reason, ConnectionRefusedError):
        message = "Connection refused"
    elif isinstance(reason, TimeoutError):
        message = "Timed out"
    elif isinstance(reason, ssl.SSLCertVerificationError):
        message = f"TLS certificate invalid: {reason.verify_message}"
    elif isinstance(reason, ssl.SSLError):
        message = f"TLS error: {reason.reason or reason}"
    else:
        message = str(reason) or type(reason).__name__
    return message[:255]
