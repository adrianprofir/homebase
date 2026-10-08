from urllib.parse import urlencode

from django import template
from django.conf import settings
from django.urls import reverse
from django.utils.html import conditional_escape, format_html

from inventory.providers.base import MISSING

register = template.Library()

# Badge tone for the statuses providers report. Anything unlisted is in transition.
STATUS_TONES = {
    "active": "ok",
    "running": "ok",
    "healthy": "ok",
    "off": "neutral",
    "stopped": "neutral",
    "inactive": "neutral",
    "initial": "neutral",
    "new": "neutral",
    "pending": "warn",
    "initializing": "warn",
    "degraded": "warn",
    "down": "bad",
    "error": "bad",
    "moved": "bad",
    "expired": "bad",
    "suspended": "bad",
    "destroyed": "bad",
    MISSING: "bad",
}
BADGE_CLASSES = {"ok": "badge-ok", "neutral": "", "warn": "badge-warn", "bad": "badge-bad"}


@register.inclusion_tag("inventory/live_status.html")
def live_status(status, provider=None, quiet=False):
    """A badge for a provider-reported status. `quiet` hides the healthy ones."""
    key = (status or "").lower()
    tone = STATUS_TONES.get(key, "bad" if key.startswith(("cancelled", "deleted")) else "warn")
    if key == MISSING:
        label = f"Missing at {provider}" if provider else "Missing"
    else:
        label = key.replace("_", " ").capitalize()
    return {
        "show": bool(key) and not (quiet and tone == "ok"),
        "label": label,
        "badge_class": BADGE_CLASSES[tone],
    }


@register.simple_tag
def admin_link(url, text, css_class="", **query):
    """A link, with `query` as its query string, or plain text in demo mode when it
    points into the admin, which a demo keeps private."""
    if settings.DEMO_MODE and url.startswith(reverse("admin:index")):
        if css_class:
            return format_html('<span class="{}">{}</span>', css_class, text)
        return conditional_escape(text)
    if query:
        url = f"{url}?{urlencode(query)}"
    if css_class:
        return format_html('<a class="{}" href="{}">{}</a>', css_class, url, text)
    return format_html('<a href="{}">{}</a>', url, text)
