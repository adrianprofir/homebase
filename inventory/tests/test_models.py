from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from inventory.models import App, Domain, Server, Subscription


@pytest.mark.django_db
class TestDomain:
    def test_name_is_stored_lowercase(self, godaddy):
        domain = Domain.objects.create(
            name=" Example.COM ", registrar=godaddy, expires_on=date(2027, 1, 1)
        )

        assert domain.name == "example.com"
        assert str(domain) == "example.com"

    def test_validation_rejects_case_insensitive_duplicate(self, godaddy):
        Domain.objects.create(name="example.com", registrar=godaddy, expires_on=date(2027, 1, 1))
        duplicate = Domain(name="EXAMPLE.com", registrar=godaddy, expires_on=date(2027, 1, 1))

        with pytest.raises(ValidationError) as excinfo:
            duplicate.full_clean()

        assert "name" in excinfo.value.message_dict

    def test_dns_provider_is_optional(self, godaddy, cloudflare):
        at_registrar = Domain.objects.create(
            name="a.example", registrar=godaddy, expires_on=date(2027, 1, 1)
        )
        on_cloudflare = Domain.objects.create(
            name="b.example",
            registrar=godaddy,
            dns_provider=cloudflare,
            expires_on=date(2027, 1, 1),
        )

        assert at_registrar.dns_provider is None
        assert list(cloudflare.dns_domains.all()) == [on_cloudflare]
        assert set(godaddy.registered_domains.all()) == {at_registrar, on_cloudflare}


@pytest.mark.django_db
class TestSubscription:
    @pytest.mark.parametrize(
        ("cycle", "cost", "monthly", "yearly"),
        [
            (Subscription.BillingCycle.MONTHLY, "6.00", "6", "72"),
            (Subscription.BillingCycle.QUARTERLY, "30.00", "10", "120"),
            (Subscription.BillingCycle.YEARLY, "24.00", "2", "24"),
            (Subscription.BillingCycle.BIENNIAL, "48.00", "2", "24"),
        ],
    )
    def test_cost_is_normalised_by_billing_cycle(self, godaddy, cycle, cost, monthly, yearly):
        sub = Subscription(
            name="Plan",
            provider=godaddy,
            cost=Decimal(cost),
            billing_cycle=cycle,
            next_renewal=date(2027, 1, 1),
        )

        assert sub.monthly_cost == Decimal(monthly)
        assert sub.yearly_cost == Decimal(yearly)

    def test_currency_defaults_to_setting(self, settings, godaddy):
        settings.HOMEBASE_DEFAULT_CURRENCY = "DKK"

        sub = Subscription.objects.create(
            name="Plan", provider=godaddy, cost=Decimal("10"), next_renewal=date(2027, 1, 1)
        )

        assert sub.currency == "DKK"

    @pytest.mark.parametrize("currency", ["usd", "US", "EURO"])
    def test_currency_must_be_iso_code(self, godaddy, currency):
        sub = Subscription(
            name="Plan",
            provider=godaddy,
            cost=Decimal("10"),
            currency=currency,
            next_renewal=date(2027, 1, 1),
        )

        with pytest.raises(ValidationError) as excinfo:
            sub.full_clean()

        assert "currency" in excinfo.value.message_dict

    def test_cost_cannot_be_negative(self, godaddy):
        sub = Subscription(
            name="Plan", provider=godaddy, cost=Decimal("-1"), next_renewal=date(2027, 1, 1)
        )

        with pytest.raises(ValidationError) as excinfo:
            sub.full_clean()

        assert "cost" in excinfo.value.message_dict

    def test_can_describe_a_domain_registration_and_a_server_plan(self, godaddy, digitalocean):
        domain = Domain.objects.create(
            name="example.com", registrar=godaddy, expires_on=date(2027, 1, 1)
        )
        droplet = Server.objects.create(name="droplet", provider=digitalocean, kind=Server.Kind.VPS)
        registration = Subscription.objects.create(
            name="example.com registration",
            provider=godaddy,
            cost=Decimal("20"),
            next_renewal=date(2027, 1, 1),
            domain=domain,
        )
        plan = Subscription.objects.create(
            name="Droplet",
            provider=digitalocean,
            cost=Decimal("6"),
            billing_cycle=Subscription.BillingCycle.MONTHLY,
            next_renewal=date(2026, 11, 1),
            server=droplet,
        )

        assert list(domain.subscriptions.all()) == [registration]
        assert list(droplet.subscriptions.all()) == [plan]


@pytest.mark.django_db
class TestApp:
    def test_checkable_skips_retired_apps_and_apps_without_url(self):
        live = App.objects.create(name="live", url="https://live.example")
        paused = App.objects.create(
            name="paused", url="https://paused.example", status=App.Status.PAUSED
        )
        App.objects.create(name="retired", url="https://old.example", status=App.Status.RETIRED)
        App.objects.create(name="no-url")

        assert set(App.checkable()) == {live, paused}

    @pytest.mark.parametrize("url", ["", "http://app.example", "https://app.example/health"])
    def test_blank_and_http_urls_are_valid(self, url):
        App(name="site", url=url).full_clean()

    @pytest.mark.parametrize("url", ["ftp://files.example/a", "ftps://files.example/a"])
    def test_url_must_be_http_or_https(self, url):
        app = App(name="files", url=url)

        with pytest.raises(ValidationError) as excinfo:
            app.full_clean()

        assert "url" in excinfo.value.message_dict

    def test_changing_url_clears_reachability(self):
        app = _checked_app()

        app.url = "https://new.example"
        app.save()
        app.refresh_from_db()

        assert app.url == "https://new.example"
        assert _reachability(app) == (None, None, None, None, "")

    def test_clearing_url_clears_reachability(self):
        app = _checked_app(last_check_ok=False, last_check_error="Connection refused")

        app.url = ""
        app.save()
        app.refresh_from_db()

        assert app.url == ""
        assert _reachability(app) == (None, None, None, None, "")

    def test_url_change_via_update_fields_clears_reachability(self):
        app = _checked_app()

        app.url = "https://new.example"
        app.save(update_fields=["url"])
        app.refresh_from_db()

        assert app.url == "https://new.example"
        assert _reachability(app) == (None, None, None, None, "")

    def test_saving_other_fields_keeps_reachability(self):
        app = _checked_app()
        checked_at = app.last_checked_at

        app.notes = "renamed in notes"
        app.save()
        app.refresh_from_db()

        assert app.notes == "renamed in notes"
        assert _reachability(app) == (checked_at, True, 200, 12, "")

    def test_update_fields_without_url_keeps_reachability_when_url_differs_in_memory(self):
        app = _checked_app()

        app.url = "https://new.example"
        app.notes = "only notes are stored"
        app.save(update_fields=["notes"])
        app.refresh_from_db()

        assert app.url == "https://old.example"
        assert app.notes == "only notes are stored"
        assert app.last_check_ok is True
        assert app.last_check_status_code == 200

    def test_deleting_server_keeps_its_apps(self, digitalocean):
        server = Server.objects.create(name="droplet", provider=digitalocean, kind=Server.Kind.VPS)
        app = App.objects.create(name="site", server=server)

        server.delete()
        app.refresh_from_db()

        assert app.server is None


def _checked_app(**overrides):
    fields = {
        "name": "site",
        "url": "https://old.example",
        "last_checked_at": timezone.now(),
        "last_check_ok": True,
        "last_check_status_code": 200,
        "last_check_response_ms": 12,
        "last_check_error": "",
    }
    fields.update(overrides)
    return App.objects.create(**fields)


def _reachability(app):
    return (
        app.last_checked_at,
        app.last_check_ok,
        app.last_check_status_code,
        app.last_check_response_ms,
        app.last_check_error,
    )
