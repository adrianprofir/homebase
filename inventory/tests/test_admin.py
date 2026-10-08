from datetime import date

import pytest
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    Alert,
    App,
    AppCheck,
    DnsRecord,
    Domain,
    Provider,
    Server,
    Subscription,
    Tunnel,
)


@pytest.fixture
def admin_inventory(godaddy, cloudflare):
    domain = Domain.objects.create(
        name="example.com", registrar=godaddy, expires_on=date(2027, 1, 1)
    )
    server = Server.objects.create(
        name="web-1", provider=godaddy, kind=Server.Kind.VPS, live_status="active"
    )
    app = App.objects.create(name="site", url="https://example.com", server=server)
    return {
        Provider: cloudflare,
        Domain: domain,
        Server: server,
        App: app,
        Subscription: Subscription.objects.create(
            name="Plan", provider=godaddy, cost=1, next_renewal=date(2027, 1, 1), external_id="x1"
        ),
        AppCheck: AppCheck.objects.create(app=app, url=app.url, checked_at=timezone.now(), ok=True),
        DnsRecord: DnsRecord.objects.create(
            domain=domain,
            external_id="r1",
            record_type="A",
            name="example.com",
            content="203.0.113.1",
            ttl=1,
        ),
        Tunnel: Tunnel.objects.create(
            provider=cloudflare, external_id="t1", name="home", status="healthy"
        ),
        Alert: Alert.objects.create(
            key="k",
            kind="downtime",
            severity="critical",
            title="site is down",
            raised_at=timezone.now(),
        ),
    }


@pytest.mark.django_db
def test_every_admin_page_renders(admin_client, admin_inventory):
    for model, obj in admin_inventory.items():
        name = f"admin:inventory_{model._meta.model_name}"

        assert admin_client.get(reverse(f"{name}_changelist")).status_code == 200, model
        assert admin_client.get(reverse(f"{name}_change", args=[obj.pk])).status_code == 200, model


@pytest.mark.django_db
def test_provider_data_cannot_be_added_or_edited(admin_client, admin_inventory):
    for model in (AppCheck, DnsRecord, Alert, Tunnel):
        assert (
            admin_client.get(reverse(f"admin:inventory_{model._meta.model_name}_add")).status_code
            == 403
        )

    record = admin_inventory[DnsRecord]
    url = reverse("admin:inventory_dnsrecord_change", args=[record.pk])
    admin_client.post(url, {"content": "198.51.100.1"})
    record.refresh_from_db()
    assert record.content == "203.0.113.1"


@pytest.mark.django_db
def test_domain_page_links_its_dns_records(admin_client, admin_inventory):
    domain = admin_inventory[Domain]

    content = admin_client.get(
        reverse("admin:inventory_domain_change", args=[domain.pk])
    ).content.decode()

    assert f'?domain__id__exact={domain.pk}">1 record</a>' in content
