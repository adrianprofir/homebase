"""PostHog analytics wiring for public-facing pages."""

from django.conf import settings


def analytics_context(request):
    """Expose analytics config to templates when a project key is set."""
    if not settings.POSTHOG_PROJECT_KEY:
        return {"analytics_enabled": False}
    if request.path.startswith("/admin"):
        return {"analytics_enabled": False}
    return {
        "analytics_enabled": True,
        "analytics_config": {
            "projectKey": settings.POSTHOG_PROJECT_KEY,
            "apiHost": settings.POSTHOG_HOST,
            "uiHost": "https://eu.posthog.com",
            "app": "homebase",
            "cookieDomain": settings.ANALYTICS_COOKIE_DOMAIN,
        },
    }
