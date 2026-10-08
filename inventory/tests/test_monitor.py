from datetime import timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.utils import timezone

from inventory import providers
from inventory.models import Alert, App, AppCheck, Domain, Provider
from inventory.monitoring import CheckResult

from .fake_api import FakeApi

pytestmark = pytest.mark.django_db

DROPLETS = {
    "droplets": [{"id": 7, "name": "web-1", "status": "active", "size": {"price_monthly": 6}}]
}


@pytest.fixture
def setup(settings, monkeypatch, godaddy):
    for name in ("CLOUDFLARE", "GODADDY", "HOSTINGER"):
        setattr(settings, f"{name}_API_TOKEN", "")
    settings.DIGITALOCEAN_API_TOKEN = "do-token"
    settings.HOMEBASE_SYNC_INTERVAL_MINUTES = 60
    settings.HOMEBASE_ALERT_EMAIL_ENABLED = True
    settings.HOMEBASE_ALERT_EMAIL_TO = ["alice@example.com"]
    api = FakeApi({"/v2/droplets": DROPLETS})
    real_client = providers.http_client
    monkeypatch.setattr(
        providers,
        "http_client",
        lambda integration, transport=None: real_client(integration, api.transport),
    )
    monkeypatch.setattr(
        "inventory.monitoring.probe",
        lambda url, timeout, ssl_context=None: CheckResult(
            ok=True, status_code=200, response_ms=12, error=""
        ),
    )
    App.objects.create(name="site", url="https://site.example")
    Domain.objects.create(
        name="soon.example", registrar=godaddy, expires_on=timezone.localdate() + timedelta(days=3)
    )
    return api


def test_runs_every_step(setup):
    out = StringIO()

    call_command("monitor", stdout=out)

    text = out.getvalue()
    assert "sync  DigitalOcean: 1 droplet, 6.00 USD a month (1 added)" in text
    assert "up    site (200, 12 ms)" in text
    assert "alerts: 1 open, 1 raised, 0 resolved; email sent" in text
    assert "monitor run finished" in text
    assert Provider.objects.get(integration="digitalocean").sync_error == ""
    assert AppCheck.objects.count() == 1
    assert Alert.objects.get().title.startswith("soon.example expires in 3 days")
    assert len(mail.outbox) == 1


def test_syncs_providers_only_when_due(setup):
    call_command("monitor", stdout=StringIO())
    requests = len(setup.requests)

    call_command("monitor", stdout=StringIO())

    assert len(setup.requests) == requests
    assert AppCheck.objects.count() == 2


def test_a_crashed_step_does_not_stop_the_others(setup, monkeypatch):
    def broken(*args):
        raise RuntimeError("disk full")

    monkeypatch.setattr("inventory.management.commands.monitor.prune_history", broken)

    with pytest.raises(CommandError, match="history cleanup"):
        call_command("monitor", stdout=StringIO())

    assert Alert.objects.exists()
    assert AppCheck.objects.exists()
