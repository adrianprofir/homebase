"""Demo mode: a public, read-only homebase on fictional data.

See `DEMO_MODE` in settings. `lab` describes the fictional lab, `seed` builds it,
`checks` simulates the reachability checks, and `api` answers the provider syncs.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def refuse_unsafe_settings():
    """Stop homebase from starting in demo mode with anything that reaches the outside.

    A provider token would let the demo read a real account, and alert delivery settings
    would let it reach the outside.
    """
    if not settings.DEMO_MODE:
        return
    from inventory.providers import INTEGRATIONS

    names = [integration.token_setting for integration in INTEGRATIONS.values()]
    alert_names = ["EMAIL_HOST", "HOMEBASE_ALERT_NTFY_TOPIC", "HOMEBASE_ALERT_WEBHOOK_URL"]
    unsafe = [name for name in [*names, *alert_names] if _setting(name)]
    if unsafe:
        raise ImproperlyConfigured(
            f"DEMO_MODE is on, so homebase refuses to start while {', '.join(unsafe)} "
            f"{'is' if len(unsafe) == 1 else 'are'} set: a demo must not reach real "
            "provider accounts or send alerts. Unset them in the demo's environment."
        )


def _setting(name):
    if name == "EMAIL_HOST":
        # Settings keep the SMTP host inside MAILERS, not under its environment name.
        options = settings.MAILERS.get("default", {}).get("OPTIONS", {})
        return options.get("host", "")
    if name == "HOMEBASE_ALERT_NTFY_TOPIC":
        return settings.HOMEBASE_ALERT_NTFY_TOPIC
    if name == "HOMEBASE_ALERT_WEBHOOK_URL":
        return settings.HOMEBASE_ALERT_WEBHOOK_URL
    return getattr(settings, name, "")
