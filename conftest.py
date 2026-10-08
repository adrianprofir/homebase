import pytest


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    # Production serves the manifest storage from STATIC_ROOT, which only exists after
    # `collectstatic`. Tests serve static files straight from the source directories.
    settings.STATIC_ROOT = None
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
