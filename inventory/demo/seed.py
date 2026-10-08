"""Build the fictional lab from `lab` into the database, for `manage.py seed_demo`.

The seed enters the inventory the way a person would in the admin, runs the real
provider sync against the canned APIs, and writes 30 days of simulated uptime history,
so every part of the dashboard is populated from the first minute. Nothing here makes
a network request, in demo mode or not.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
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
from inventory.providers import INTEGRATIONS, sync
from inventory.providers.digitalocean import _first_of_next_month

from . import api, lab
from .checks import simulated_probe

HISTORY_DAYS = 30
# Every model `seed` writes, in an order that deletes cleanly.
INVENTORY = [Alert, Subscription, App, Tunnel, DnsRecord, Domain, Server, Provider]


class SeedError(Exception):
    pass


def inventory_is_empty():
    return not any(model.objects.exists() for model in INVENTORY)


def reset():
    """Delete the whole inventory. Users and their sessions stay."""
    for model in INVENTORY:
        model.objects.all().delete()


@transaction.atomic
def seed(now=None):
    """Build the lab. Returns a one-line summary."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    providers = {
        p.name: Provider.objects.create(
            name=p.name,
            website=p.website,
            integration=p.integration,
            account_notes=p.account_notes,
        )
        for p in lab.PROVIDERS
    }
    domains = {}
    for d in lab.DOMAINS:
        expires_on = today + timedelta(days=d.expires_in_days)
        domain = Domain.objects.create(
            name=d.name,
            registrar=providers[d.registrar],
            expires_on=expires_on,
            auto_renew=d.auto_renew,
            notes=d.notes,
        )
        domains[d.name] = domain
        Subscription.objects.create(
            name=f"{d.name} registration",
            provider=providers[d.registrar],
            cost=d.yearly_cost,
            currency=d.currency,
            billing_cycle=Subscription.BillingCycle.YEARLY,
            next_renewal=expires_on,
            auto_renew=d.auto_renew,
            domain=domain,
        )
    servers = {
        s.name: Server.objects.create(
            name=s.name,
            provider=providers.get(s.provider),
            kind=s.kind,
            host=s.host,
            location=s.location,
            notes=s.notes,
            live_status=s.live_status,
        )
        for s in lab.SERVERS
    }
    for s in lab.SUBSCRIPTIONS:
        if s.renews_in_days is None:
            next_renewal = _first_of_next_month(today)
        else:
            next_renewal = today + timedelta(days=s.renews_in_days)
        Subscription.objects.create(
            name=s.name,
            provider=providers[s.provider],
            cost=s.cost,
            currency=s.currency,
            billing_cycle=s.billing_cycle,
            next_renewal=next_renewal,
            auto_renew=s.auto_renew,
            server=servers.get(s.server),
            notes=s.notes,
        )
    apps = [
        (
            a,
            App.objects.create(
                name=a.name,
                hostname=a.hostname,
                server=servers.get(a.server),
                url=a.url,
                repo_url=a.repo_url,
                status=a.status,
                notes=a.notes,
            ),
        )
        for a in lab.APPS
    ]

    _sync_providers(providers, now)
    Tunnel.objects.filter(name=lab.CLOUDFLARE_TUNNEL["name"]).update(
        server=servers[lab.HOME_SERVER]
    )
    checks = _write_history([app for _, app in apps if app.url], now)
    return (
        f"Seeded {len(providers)} providers, {len(domains)} domains, {len(servers)} servers, "
        f"{Subscription.objects.filter(active=True).count()} active subscriptions, "
        f"{len(apps)} apps, and {checks} uptime checks over {HISTORY_DAYS} days."
    )


def _sync_providers(providers, now):
    """Run the real sync against the canned APIs, as the monitor will later."""
    hostinger = providers["Hostinger"]
    # The demo's Hostinger token "stopped working" after an earlier good sync.
    last_good = now - timedelta(days=lab.HOSTINGER_LAST_GOOD_SYNC_DAYS_AGO)
    hostinger.sync_attempted_at = last_good
    hostinger.sync_succeeded_at = last_good
    hostinger.sync_summary = lab.HOSTINGER_LAST_SUMMARY
    hostinger.save()
    for integration in INTEGRATIONS.values():
        provider = sync(integration, now=now, transport=api.transport(now))
        failed = bool(provider.sync_error)
        if failed != (integration.key == "hostinger"):
            raise SeedError(
                f"The demo {integration.label} sync "
                f"{'failed: ' + provider.sync_error if failed else 'should fail but succeeded'}"
            )


def _write_history(apps, now):
    """Simulated checks every check interval for 30 days, and each app's latest state."""
    step = timedelta(seconds=settings.HOMEBASE_CHECK_INTERVAL_SECONDS)
    count = int(timedelta(days=HISTORY_DAYS) / step)
    times = [now - step * index for index in range(count, 0, -1)]
    total = 0
    for app in apps:
        rows = []
        down_since = None
        for at in times:
            result = simulated_probe(app.url, at, seeded_at=now)
            down_since = None if result.ok else down_since or at
            rows.append(
                AppCheck(
                    app=app,
                    url=app.url,
                    checked_at=at,
                    ok=result.ok,
                    status_code=result.status_code,
                    response_ms=result.response_ms,
                    error=result.error,
                    tls_expires_at=result.tls_expires_at,
                )
            )
        AppCheck.objects.bulk_create(rows, batch_size=2000)
        total += len(rows)
        last = rows[-1]
        App.objects.filter(pk=app.pk).update(
            last_checked_at=last.checked_at,
            last_check_ok=last.ok,
            last_check_status_code=last.status_code,
            last_check_response_ms=last.response_ms,
            last_check_error=last.error,
            last_check_tls_expires_at=last.tls_expires_at,
            down_since=down_since,
            monitoring_since=times[0],
        )
    return total
