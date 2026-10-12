import json
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "analytics_consent_harness.mjs"
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.fixture
def staff_user(db):
    user = get_user_model().objects.create_user("staff", password="not-a-real-password")
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


@pytest.mark.django_db
def test_analytics_absent_without_project_key(client):
    response = client.get(reverse("login"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "analytics-config" not in content
    assert "analytics-consent" not in content
    assert "Analytics preferences" not in content
    assert "posthog" not in content.lower()


@pytest.mark.django_db
def test_analytics_present_on_public_pages_when_key_set(client, settings):
    settings.POSTHOG_PROJECT_KEY = "phc_test"
    response = client.get(reverse("login"))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="analytics-config"' in content
    assert "analytics-consent" in content
    assert "Analytics preferences" in content
    assert "/static/js/analytics.js" in content
    marker = 'id="analytics-config" type="application/json">'
    config = json.loads(content.split(marker, 1)[1].split("</script>", 1)[0].strip())
    assert config["projectKey"] == "phc_test"
    assert config["app"] == "homebase"


@pytest.mark.django_db
def test_analytics_absent_on_admin_login(client, settings):
    settings.POSTHOG_PROJECT_KEY = "phc_test"
    response = client.get("/admin/login/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "analytics-config" not in content
    assert "Analytics preferences" not in content


def test_analytics_consent_harness_js():
    result = subprocess.run(
        ["node", str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.django_db
def test_analytics_absent_on_admin_index(client, settings, staff_user):
    settings.POSTHOG_PROJECT_KEY = "phc_test"
    client.force_login(staff_user)
    response = client.get("/admin/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "analytics-config" not in content
    assert "Analytics preferences" not in content
