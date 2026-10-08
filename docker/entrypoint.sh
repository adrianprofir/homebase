#!/bin/sh
set -eu

# Only the web service applies migrations, so other services sharing this image
# never race it. They start after web reports healthy.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
