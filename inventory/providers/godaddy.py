"""GoDaddy: registered domains with their expiry date and auto-renew setting.

Uses a Personal Access Token with the domains.domain:read scope on GET /v1/domains.
"""

from dataclasses import dataclass
from datetime import date

from inventory.models import Domain, Subscription

from .base import MISSING, ProviderError, get_json, plural, utc_date

PAGE_SIZE = 100
# Statuses of domains that are no longer held in the account.
GONE_STATUSES = ("CANCELLED", "DELETED", "EXPIRED_REASSIGNED", "REPOSSESSED", "TRANSFERRED_OUT")


@dataclass(frozen=True)
class DomainInfo:
    name: str
    status: str
    expires: date | None
    auto_renew: bool

    @property
    def held(self):
        return not self.status.upper().startswith(GONE_STATUSES)


def fetch(http):
    domains = []
    marker = None
    while True:
        params = {"limit": PAGE_SIZE}
        if marker:
            params["marker"] = marker
        page = get_json(http, "v1/domains", params)
        if not isinstance(page, list):
            raise ProviderError("v1/domains did not return a list of domains")
        domains += [
            DomainInfo(
                name=item["domain"].strip().lower(),
                status=item.get("status") or "",
                expires=utc_date(item.get("expires")),
                auto_renew=bool(item.get("renewAuto")),
            )
            for item in page
        ]
        if len(page) < PAGE_SIZE:
            return domains
        marker = page[-1]["domain"]


def apply(provider, domains, now):
    added = 0
    held = [info for info in domains if info.held]
    for info in domains:
        domain = Domain.objects.filter(name=info.name).first()
        if not info.held:
            # Released or transferred away: record that, but leave the dates alone.
            if domain is not None and domain.registrar_id == provider.pk:
                domain.registrar_status = info.status
                domain.save(update_fields=["registrar_status"])
            continue
        if domain is None:
            if info.expires is None:
                continue
            domain = Domain(name=info.name, registrar=provider, expires_on=info.expires)
            added += 1
        domain.registrar = provider
        if info.expires is not None:
            domain.expires_on = info.expires
        domain.auto_renew = info.auto_renew
        domain.registrar_status = info.status
        domain.save()
        # A domain registration renews when the domain expires.
        Subscription.objects.filter(domain=domain, provider=provider, active=True).update(
            next_renewal=domain.expires_on, auto_renew=domain.auto_renew
        )

    missing = list(
        Domain.objects.filter(registrar=provider)
        .exclude(name__in=[info.name for info in domains])
        .values_list("name", flat=True)
    )
    Domain.objects.filter(name__in=missing).update(registrar_status=MISSING)

    summary = plural(len(held), "domain")
    if added:
        summary += f" ({added} added)"
    if missing:
        summary += f"; not in the account any more: {', '.join(missing)}"
    return summary
