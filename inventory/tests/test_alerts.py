import smtplib
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from inventory.alerts import current_alerts, duration, send_alert_email, update_alerts
from inventory.models import Alert, App, Domain, Provider, Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def alert_settings(settings):
    settings.HOMEBASE_EXPIRY_ALERT_DAYS = 30
    settings.HOMEBASE_TLS_ALERT_DAYS = 10
    settings.HOMEBASE_DOWNTIME_ALERT_MINUTES = 10
    settings.HOMEBASE_ALERT_EMAIL_ENABLED = False
    settings.HOMEBASE_ALERT_EMAIL_TO = ["alice@example.com"]
    settings.HOMEBASE_DASHBOARD_URL = "http://192.0.2.10:8090/"
    for name in ("CLOUDFLARE", "GODADDY", "DIGITALOCEAN", "HOSTINGER"):
        setattr(settings, f"{name}_API_TOKEN", "")
    return settings


@pytest.fixture
def email_on(alert_settings):
    alert_settings.HOMEBASE_ALERT_EMAIL_ENABLED = True
    return alert_settings


def in_days(days):
    return timezone.localdate() + timedelta(days=days)


def domain(godaddy, days, auto_renew=False, name="example.com"):
    return Domain.objects.create(
        name=name, registrar=godaddy, expires_on=in_days(days), auto_renew=auto_renew
    )


def subscription(provider, days, auto_renew=False, **fields):
    return Subscription.objects.create(
        name=fields.pop("name", "Hosting"),
        provider=provider,
        cost=Decimal("10"),
        next_renewal=in_days(days),
        auto_renew=auto_renew,
        **fields,
    )


def summary(now=None):
    return [(a.kind, a.severity) for a in current_alerts(now or timezone.now())]


class TestDomainExpiry:
    @pytest.mark.parametrize(
        ("days", "auto_renew", "expected"),
        [
            (31, False, []),
            (30, False, [("domain_expiry", "warning")]),
            (7, False, [("domain_expiry", "critical")]),
            (-1, False, [("domain_expiry", "critical")]),
            (20, True, []),
            (7, True, [("domain_expiry", "warning")]),
            (-1, True, [("domain_expiry", "critical")]),
        ],
    )
    def test_thresholds(self, godaddy, days, auto_renew, expected):
        domain(godaddy, days, auto_renew)

        assert summary() == expected

    def test_title_and_link(self, godaddy):
        item = domain(godaddy, 5)

        [alert] = current_alerts(timezone.now())

        assert alert.title == (
            f"example.com expires in 5 days ({in_days(5):%Y-%m-%d}), auto-renew is off"
        )
        assert alert.link == reverse("admin:inventory_domain_change", args=[item.pk])


class TestSubscriptionRenewal:
    @pytest.mark.parametrize(
        ("days", "auto_renew", "expected"),
        [
            (31, False, []),
            (12, False, [("subscription_renewal", "warning")]),
            (3, False, [("subscription_renewal", "critical")]),
            (3, True, []),
            (-2, True, [("subscription_renewal", "warning")]),
        ],
    )
    def test_thresholds(self, godaddy, days, auto_renew, expected):
        subscription(godaddy, days, auto_renew)

        assert summary() == expected

    def test_inactive_subscriptions_never_alert(self, godaddy):
        subscription(godaddy, -10, active=False)

        assert summary() == []

    def test_domain_registrations_leave_it_to_the_domain_alert(self, godaddy):
        item = domain(godaddy, 3)
        subscription(godaddy, 3, domain=item)

        assert summary() == [("domain_expiry", "critical")]

    def test_overdue_auto_renewal_asks_for_an_update(self, godaddy):
        subscription(godaddy, -2, auto_renew=True)

        [alert] = current_alerts(timezone.now())

        assert (
            alert.title
            == "Hosting (GoDaddy) renewal date passed 2 days ago; update it once renewed"
        )


class TestAppAlerts:
    def test_tls_expiry_thresholds_for_live_https_apps(self):
        now = timezone.now()
        App.objects.create(
            name="soon",
            url="https://soon.example",
            last_check_tls_expires_at=now + timedelta(days=9, hours=1),
        )
        App.objects.create(
            name="urgent",
            url="https://urgent.example",
            last_check_tls_expires_at=now + timedelta(days=2),
        )
        App.objects.create(
            name="fine",
            url="https://fine.example",
            last_check_tls_expires_at=now + timedelta(days=11),
        )
        App.objects.create(
            name="paused",
            url="https://paused.example",
            status=App.Status.PAUSED,
            last_check_tls_expires_at=now + timedelta(days=1),
        )

        alerts = current_alerts(now)

        assert [(a.title.split(" ")[3], a.severity) for a in alerts] == [
            ("urgent", "critical"),
            ("soon", "warning"),
        ]
        assert alerts[1].title.startswith("TLS certificate of soon expires in 9 days")

    def test_expired_certificate(self):
        now = timezone.now()
        App.objects.create(
            name="site",
            url="https://site.example",
            last_check_tls_expires_at=now - timedelta(hours=1),
        )

        [alert] = current_alerts(now)

        assert alert.critical
        assert alert.title.startswith("TLS certificate of site has expired")

    def test_sustained_downtime_only(self):
        now = timezone.now()
        down = App.objects.create(
            name="blog",
            url="https://blog.example",
            down_since=now - timedelta(minutes=25),
            last_check_ok=False,
            last_check_error="Connection refused",
        )
        App.objects.create(
            name="blip", url="https://blip.example", down_since=now - timedelta(minutes=5)
        )
        App.objects.create(
            name="dev",
            url="https://dev.example",
            status=App.Status.DEVELOPMENT,
            down_since=now - timedelta(hours=5),
        )

        [alert] = current_alerts(now)

        assert (alert.kind, alert.severity) == ("downtime", "critical")
        assert alert.title == "blog has been down for 25 min (Connection refused)"
        assert alert.link == reverse("app_detail", args=[down.pk])


class TestSyncFailure:
    def test_only_configured_providers_alert(self, alert_settings):
        Provider.objects.create(
            name="Cloudflare",
            integration="cloudflare",
            sync_error="HTTP 403 from zones: Authentication error",
        )
        Provider.objects.create(name="GoDaddy", integration="godaddy", sync_error="old error")
        alert_settings.CLOUDFLARE_API_TOKEN = "cf"

        [alert] = current_alerts(timezone.now())

        assert (
            alert.title == "Cloudflare sync is failing: HTTP 403 from zones: Authentication error"
        )
        assert alert.severity == "warning"


@pytest.mark.parametrize(
    ("delta", "text"),
    [
        (timedelta(minutes=12), "12 min"),
        (timedelta(hours=3), "3 h"),
        (timedelta(hours=3, minutes=5), "3 h 5 min"),
        (timedelta(days=4, hours=2), "4 days"),
    ],
)
def test_duration(delta, text):
    assert duration(delta) == text


class TestUpdateAlerts:
    def test_raises_escalates_and_resolves(self, godaddy):
        item = domain(godaddy, 20)
        now = timezone.now()

        first = update_alerts(now)
        alert = Alert.objects.get()
        assert (first.raised, first.open, alert.severity) == (1, 1, "warning")

        item.expires_on = in_days(3)
        item.save()
        second = update_alerts(now + timedelta(minutes=5))
        alert.refresh_from_db()
        assert (second.raised, alert.severity, alert.raised_at) == (0, "critical", now)

        item.expires_on = in_days(400)
        item.save()
        third = update_alerts(now + timedelta(minutes=10))
        alert.refresh_from_db()
        assert third.resolved == 1
        assert alert.resolved_at == now + timedelta(minutes=10)
        assert third.email == "off"

    def test_a_cleared_alert_that_comes_back_is_new(self, godaddy):
        item = domain(godaddy, 20)
        update_alerts()
        item.expires_on = in_days(400)
        item.save()
        update_alerts()
        item.expires_on = in_days(20)
        item.save()

        update_alerts()

        assert Alert.objects.filter(resolved_at=None).count() == 1
        assert Alert.objects.count() == 2

    def test_old_resolved_alerts_are_dropped(self):
        now = timezone.now()
        Alert.objects.create(
            key="x",
            kind="downtime",
            severity="critical",
            title="old",
            raised_at=now - timedelta(days=200),
            resolved_at=now - timedelta(days=91),
        )

        update_alerts(now)

        assert not Alert.objects.exists()


class TestAlertEmail:
    def test_no_email_when_not_configured(self, godaddy):
        domain(godaddy, 3)

        update_alerts()

        assert mail.outbox == []

    def test_one_email_per_change(self, email_on, godaddy):
        item = domain(godaddy, 20)
        subscription(godaddy, 3)

        result = update_alerts()
        assert result.email == "sent"
        [message] = mail.outbox
        assert message.to == ["alice@example.com"]
        assert message.subject == "[homebase] 2 new alerts"
        assert "CRITICAL  Hosting (GoDaddy) renews in 3 days" in message.body
        assert "WARNING   example.com expires in 20 days" in message.body
        assert f"http://192.0.2.10:8090/admin/inventory/domain/{item.pk}/change/" in message.body

        assert update_alerts().email == "nothing to send"
        assert len(mail.outbox) == 1

        item.expires_on = in_days(5)
        item.save()
        update_alerts()
        assert mail.outbox[-1].subject.startswith("[homebase] example.com expires in 5 days")

        item.expires_on = in_days(400)
        item.save()
        update_alerts()
        assert mail.outbox[-1].subject.startswith("[homebase] Resolved: example.com expires")
        assert len(mail.outbox) == 3

    def test_resolution_of_an_alert_never_emailed_sends_nothing(self, alert_settings, godaddy):
        item = domain(godaddy, 3)
        update_alerts()
        alert_settings.HOMEBASE_ALERT_EMAIL_ENABLED = True
        Alert.objects.update(emailed_severity="")
        item.expires_on = in_days(400)
        item.save()

        assert update_alerts().email == "nothing to send"
        assert mail.outbox == []

    def test_failed_email_is_recorded_shown_and_retried(
        self, email_on, godaddy, monkeypatch, client, django_user_model
    ):
        domain(godaddy, 3)

        def refuse(self, *args, **kwargs):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        monkeypatch.setattr("django.core.mail.EmailMessage.send", refuse)
        result = update_alerts()

        assert result.email.startswith("failed:")
        alert = Alert.objects.get()
        assert "SMTPAuthenticationError" in alert.email_error
        assert alert.emailed_severity == ""
        client.force_login(django_user_model.objects.create_user("alice", password="x"))
        assert "Alert email failed:" in client.get(reverse("dashboard")).content.decode()

        monkeypatch.undo()
        assert update_alerts().email == "sent"
        alert.refresh_from_db()
        assert (alert.email_error, alert.emailed_severity) == ("", "critical")

    def test_failed_initial_email_clears_with_the_alert(
        self, email_on, godaddy, monkeypatch, client, django_user_model
    ):
        item = domain(godaddy, 3)
        client.force_login(django_user_model.objects.create_user("alice", password="x"))

        def refuse(self, *args, **kwargs):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        monkeypatch.setattr("django.core.mail.EmailMessage.send", refuse)
        assert update_alerts().email.startswith("failed:")
        assert "Alert email failed:" in client.get(reverse("dashboard")).content.decode()

        item.expires_on = in_days(400)
        item.save()
        assert update_alerts().email == "nothing to send"

        alert = Alert.objects.get()
        assert alert.resolved_at is not None
        assert alert.emailed_severity == ""
        assert alert.email_error == ""
        response = client.get(reverse("dashboard"))
        assert response.context["email_error"] is None
        assert "Alert email failed:" not in response.content.decode()
        assert mail.outbox == []

    @pytest.mark.parametrize(
        ("severity", "emailed", "resolved", "resolution_emailed", "pending"),
        [
            ("warning", "", False, False, True),
            ("critical", "", False, False, True),
            ("critical", "warning", False, False, True),
            ("warning", "warning", False, False, False),
            ("critical", "critical", False, False, False),
            ("warning", "critical", False, False, False),
            ("critical", "", True, False, False),
            ("critical", "warning", True, False, True),
            ("warning", "critical", True, False, True),
            ("critical", "critical", True, True, False),
        ],
    )
    def test_dashboard_email_errors_match_pending_sends(
        self,
        email_on,
        client,
        django_user_model,
        severity,
        emailed,
        resolved,
        resolution_emailed,
        pending,
    ):
        now = timezone.now()
        Alert.objects.create(
            key="test",
            kind=Alert.Kind.DOMAIN_EXPIRY,
            title="example.com expires",
            severity=severity,
            emailed_severity=emailed,
            raised_at=now,
            resolved_at=now if resolved else None,
            resolution_emailed=resolution_emailed,
            email_error="SMTP unavailable",
        )
        client.force_login(django_user_model.objects.create_user("alice", password="x"))

        response = client.get(reverse("dashboard"))

        assert bool(response.context["email_error"]) == pending
        assert ("Alert email failed:" in response.content.decode()) == pending
        assert send_alert_email(now) == ("sent" if pending else "nothing to send")
        assert len(mail.outbox) == int(pending)

    def test_command_reports_the_outcome(self, email_on, godaddy):
        domain(godaddy, 3)
        out = StringIO()
        call_command("update_alerts", stdout=out)
        assert "1 open alert(s): 1 raised, 0 resolved; email sent" in out.getvalue()
