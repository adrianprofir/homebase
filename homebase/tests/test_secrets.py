import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from homebase.secrets import PLACEHOLDER_SECRET, refuse_placeholder_secrets

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


class TestRefusePlaceholderSecrets:
    def test_allows_placeholders_in_debug(self, settings):
        settings.DEBUG = True
        settings.SECRET_KEY = PLACEHOLDER_SECRET
        settings.DATABASES["default"]["PASSWORD"] = PLACEHOLDER_SECRET

        refuse_placeholder_secrets()

    def test_refuses_placeholder_secret_key(self, settings):
        settings.DEBUG = False
        settings.SECRET_KEY = PLACEHOLDER_SECRET
        settings.DATABASES["default"]["PASSWORD"] = "real-password"

        with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
            refuse_placeholder_secrets()

    def test_refuses_placeholder_database_password(self, settings):
        settings.DEBUG = False
        settings.SECRET_KEY = "a-real-secret"
        settings.DATABASES["default"]["PASSWORD"] = PLACEHOLDER_SECRET

        with pytest.raises(ImproperlyConfigured, match="database password"):
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
