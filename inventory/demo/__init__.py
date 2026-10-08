"""Demo mode: a public, read-only homebase on fictional data.

See `DEMO_MODE` in settings. `lab` describes the fictional lab, `seed` builds it,
`checks` simulates the reachability checks, and `api` answers the provider syncs.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def refuse_unsafe_settings():
    """Stop homebase from starting in demo mode with anything that reaches the outside.

    A provider token would let the demo read a real account, and EMAIL_HOST would let it
    send mail.
    """
    if not settings.DEMO_MODE:
        return
    from inventory.providers import INTEGRATIONS

    names = [integration.token_setting for integration in INTEGRATIONS.values()]
    unsafe = [name for name in [*names, "EMAIL_HOST"] if _setting(name)]
    if unsafe:
        raise ImproperlyConfigured(
            f"DEMO_MODE is on, so homebase refuses to start while {', '.join(unsafe)} "
            f"{'is' if len(unsafe) == 1 else 'are'} set: a demo must not reach real "
            "provider accounts or send email. Unset them in the demo's environment."
        )


def _setting(name):
    if name == "EMAIL_HOST":
        # Settings keep the SMTP host inside MAILERS, not under its environment name.
        options = settings.MAILERS.get("default", {}).get("OPTIONS", {})
        return options.get("host", "")
    return getattr(settings, name, "")
