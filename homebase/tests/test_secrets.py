import os
import subprocess
import sys
from pathlib import Path

from homebase.secrets import (
    PLACEHOLDER_SECRET,
    placeholder_secret_fields,
    refuse_placeholder_secrets,
)

BASE_DIR = Path(__file__).resolve().parents[2]


def manage(*args, **env):
    return subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", *args],
        cwd=BASE_DIR,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestPlaceholderSecretFields:
    def test_allows_placeholders_in_debug(self):
        assert placeholder_secret_fields(True, PLACEHOLDER_SECRET, PLACEHOLDER_SECRET) == []

    def test_refuses_placeholder_secret_key(self):
        assert placeholder_secret_fields(False, PLACEHOLDER_SECRET, "real-password") == [
            "SECRET_KEY"
        ]

    def test_refuses_placeholder_database_password(self):
        assert placeholder_secret_fields(False, "a-real-secret", PLACEHOLDER_SECRET) == [
            "database password (POSTGRES_PASSWORD / DATABASE_URL)"
        ]


class TestRefusePlaceholderSecrets:
    def test_allows_placeholders_in_debug(self, settings):
        settings.DEBUG = True
        settings.SECRET_KEY = PLACEHOLDER_SECRET

        refuse_placeholder_secrets()

    def test_manage_py_will_not_start_with_placeholders(self):
        result = manage(
            "check",
            DEBUG="false",
            SECRET_KEY=PLACEHOLDER_SECRET,
            DATABASE_URL="postgres://homebase:change-me@localhost:5432/homebase",
        )

        assert result.returncode != 0
        assert "refuses to start with placeholder secrets" in result.stderr
