"""Read-only sync of live data from provider APIs into the inventory.

Each provider module has `fetch(http)`, which only calls the API, and
`apply(provider, data, now)`, which only writes the database and returns a one-line
summary. `sync()` runs both for one provider, applies the result in one transaction,
and records the outcome on the Provider row, so a failure shows on the dashboard and
never leaves half an update behind.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from types import ModuleType

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from inventory.demo import api as demo_api
from inventory.models import Provider

from . import cloudflare, digitalocean, godaddy, hostinger
from .base import USER_AGENT, ProviderError

logger = logging.getLogger("homebase.providers")


@dataclass(frozen=True)
class Integration:
    key: str
    token_setting: str
    base_url: str
    website: str
    module: ModuleType

    @property
    def label(self):
        return Provider.Integration(self.key).label

    @property
    def configured(self):
        # Demo mode syncs every provider against canned responses, without a token.
        return settings.DEMO_MODE or bool(getattr(settings, self.token_setting))


INTEGRATIONS = {
    integration.key: integration
    for integration in [
        Integration(
            "cloudflare",
            "CLOUDFLARE_API_TOKEN",
            "https://api.cloudflare.com/client/v4/",
            "https://www.cloudflare.com",
            cloudflare,
        ),
        Integration(
            "digitalocean",
            "DIGITALOCEAN_API_TOKEN",
            "https://api.digitalocean.com/v2/",
            "https://www.digitalocean.com",
            digitalocean,
        ),
        Integration(
            "godaddy",
            "GODADDY_API_TOKEN",
            "https://api.godaddy.com/",
            "https://www.godaddy.com",
            godaddy,
        ),
        Integration(
            "hostinger",
            "HOSTINGER_API_TOKEN",
            "https://developers.hostinger.com/api/",
            "https://www.hostinger.com",
            hostinger,
        ),
    ]
}


def provider_for(integration):
    """The Provider row an integration writes to, linking or creating it if needed."""
    provider = Provider.objects.filter(integration=integration.key).first()
    if provider is not None:
        return provider
    provider = Provider.objects.filter(name__iexact=integration.label, integration="").first()
    if provider is not None:
        provider.integration = integration.key
        provider.save(update_fields=["integration"])
        return provider
    return Provider.objects.create(
        name=integration.label, website=integration.website, integration=integration.key
    )


def due_integrations(now):
    """Configured integrations whose last sync attempt is older than the sync interval."""
    cutoff = now - timedelta(minutes=settings.HOMEBASE_SYNC_INTERVAL_MINUTES)
    recent = set(
        Provider.objects.filter(~Q(integration=""), sync_attempted_at__gt=cutoff).values_list(
            "integration", flat=True
        )
    )
    return [i for i in INTEGRATIONS.values() if i.configured and i.key not in recent]


def http_client(integration, transport=None):
    if transport is None and settings.DEMO_MODE:
        # A demo never calls a real provider API.
        transport = demo_api.transport()
    token = getattr(settings, integration.token_setting)
    return httpx.Client(
        base_url=integration.base_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        timeout=settings.HOMEBASE_SYNC_TIMEOUT_SECONDS,
        transport=transport,
    )


def sync(integration, now=None, transport=None):
    """Fetch one provider's live data and store it. Returns the updated Provider row."""
    now = now or timezone.now()
    provider = provider_for(integration)
    try:
        with http_client(integration, transport) as http:
            data = integration.module.fetch(http)
        with transaction.atomic():
            summary = integration.module.apply(provider, data, now)
    except ProviderError as exc:
        error = str(exc)
    except Exception as exc:
        logger.exception("%s sync crashed", integration.label)
        error = f"Unexpected error: {type(exc).__name__}: {exc}"
    else:
        error = ""
    provider.sync_attempted_at = now
    provider.sync_error = error
    if error:
        logger.warning("%s sync failed: %s", integration.label, error)
    else:
        provider.sync_succeeded_at = now
        provider.sync_summary = summary[:255]
        logger.info("%s sync: %s", integration.label, summary)
    provider.save(
        update_fields=["sync_attempted_at", "sync_succeeded_at", "sync_error", "sync_summary"]
    )
    return provider
