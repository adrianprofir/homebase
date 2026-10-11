"""Refuse known placeholder secrets from `.env.example` in non-debug deployments."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PLACEHOLDER_SECRET = "change-me"  # noqa: S105


def placeholder_secret_fields(debug, secret_key, db_password):
    """Return env field names that still use the example placeholder."""
    if debug:
        return []
    unsafe = []
    if secret_key == PLACEHOLDER_SECRET:
        unsafe.append("SECRET_KEY")
    if (db_password or "") == PLACEHOLDER_SECRET:
        unsafe.append("database password (POSTGRES_PASSWORD / DATABASE_URL)")
    return unsafe


def refuse_placeholder_secrets():
    """Stop homebase from running with example secrets when DEBUG is off."""
    unsafe = placeholder_secret_fields(
        settings.DEBUG,
        settings.SECRET_KEY,
        settings.DATABASES["default"].get("PASSWORD") or "",
    )
    if not unsafe:
        return
    message = (
        "homebase refuses to start with placeholder secrets from .env.example: "
        f"{', '.join(unsafe)} still {'is' if len(unsafe) == 1 else 'are'} "
        f'"{PLACEHOLDER_SECRET}". Generate real values before running with DEBUG=false.'
    )
    if any("database password" in field for field in unsafe):
        message += (
            " Postgres only reads POSTGRES_PASSWORD when the pgdata volume is first created; "
            "after changing it in .env you must also run ALTER USER on the database role "
            "(see docs/deploy-home-server.md)."
        )
    else:
        message += " See README.md."
    raise ImproperlyConfigured(message)
