"""Plumbing shared by the provider modules: API reads, and matching servers and plans."""

from datetime import UTC

import httpx
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from inventory.models import Server, Subscription

USER_AGENT = "homebase-provider-sync/1.0"
MISSING = "missing"


class ProviderError(Exception):
    """A provider API call failed. The message is shown on the dashboard, so no secrets."""


def get_json(http, path, params=None):
    """GET `path` from the provider API and return the decoded JSON body."""
    try:
        response = http.get(path, params=params)
    except httpx.TimeoutException as exc:
        raise ProviderError(f"Timed out calling {path}") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not call {path}: {exc}") from exc
    if response.status_code >= 400:
        raise ProviderError(f"HTTP {response.status_code} from {path}: {api_message(response)}")
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(f"{path} did not return JSON") from exc


def api_message(response):
    """The error text a provider put in its response body, if any."""
    try:
        body = response.json()
    except ValueError:
        body = None
    message = ""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            message = "; ".join(
                str(error.get("message", error)) if isinstance(error, dict) else str(error)
                for error in errors
            )
        else:
            message = next(
                (str(body[key]) for key in ("message", "detail", "error") if body.get(key)), ""
            )
    return (message or response.reason_phrase or "no details")[:300]


def utc_date(value):
    """The UTC calendar date of an ISO 8601 timestamp, or None."""
    moment = parse_datetime(value) if value else None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


def match_server(provider, external_id, name, hosts):
    """The inventory server for a provider machine: linked by ID, else by name or address."""
    server = Server.objects.filter(provider=provider, external_id=external_id).first()
    if server is not None:
        return server
    hosts = [host for host in hosts if host]
    return (
        Server.objects.filter(provider=provider, external_id="")
        .filter(Q(name__iexact=name) | Q(host__in=hosts))
        .order_by("pk")
        .first()
    )


def unused_server_name(name, provider):
    limit = Server._meta.get_field("name").max_length
    candidate = name[:limit]
    suffix = f" ({provider.name[: limit - 4]})"
    counter = 0
    while Server.objects.filter(name=candidate).exists():
        candidate = f"{name[: limit - len(suffix)]}{suffix}"
        counter += 1
        suffix = f" ({provider.pk}-{counter})"
    return candidate


def sync_plan(provider, external_id, *, server, name, values):
    """Create or update the subscription the provider bills under `external_id`.

    The first sync adopts a subscription entered by hand for the same server, or with
    the same name, so its notes and history stay. Returns the subscription, or None
    when there is nothing to adopt and the plan is no longer active.
    """
    name = name[: Subscription._meta.get_field("name").max_length]
    subscription = Subscription.objects.filter(provider=provider, external_id=external_id).first()
    if subscription is None:
        unlinked = Subscription.objects.filter(provider=provider, external_id="", active=True)
        by_server = list(unlinked.filter(server=server)[:2]) if server else []
        by_name = list(unlinked.filter(name__iexact=name)[:2])
        if len(by_server) == 1:
            subscription = by_server[0]
        elif not by_server and len(by_name) == 1:
            subscription = by_name[0]
    if subscription is None:
        if not values.get("active", True):
            return None
        subscription = Subscription(provider=provider, name=name)
    subscription.external_id = external_id
    if server is not None:
        subscription.server = server
    for field, value in values.items():
        setattr(subscription, field, value)
    subscription.save()
    return subscription


def plural(count, noun, plural_noun=None):
    return f"{count} {noun if count == 1 else plural_noun or noun + 's'}"
