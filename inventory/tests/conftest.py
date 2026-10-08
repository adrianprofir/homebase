import pytest

from inventory.models import Provider


@pytest.fixture
def godaddy(db):
    return Provider.objects.create(name="GoDaddy", website="https://www.godaddy.com")


@pytest.fixture
def cloudflare(db):
    return Provider.objects.create(name="Cloudflare", website="https://www.cloudflare.com")


@pytest.fixture
def digitalocean(db):
    return Provider.objects.create(name="DigitalOcean")
