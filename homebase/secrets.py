"""Refuse known placeholder secrets from `.env.example` in non-debug deployments."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PLACEHOLDER_SECRET = "change-me"  # noqa: S105


def refuse_placeholder_secrets():
    """Stop homebase from running with example secrets when DEBUG is off."""
    if settings.DEBUG:
        return
    unsafe = []
    if settings.SECRET_KEY == PLACEHOLDER_SECRET:
        unsafe.append("SECRET_KEY")
    password = settings.DATABASES["default"].get("PASSWORD") or ""
    if password == PLACEHOLDER_SECRET:
        unsafe.append("database password (POSTGRES_PASSWORD / DATABASE_URL)")
    if not unsafe:
        return
    raise ImproperlyConfigured(
        "homebase refuses to start with placeholder secrets from .env.example: "
        f"{', '.join(unsafe)} still {'is' if len(unsafe) == 1 else 'are'} "
        f'"{PLACEHOLDER_SECRET}". Generate real values before running with DEBUG=false. '
        "See README.md."
    )
