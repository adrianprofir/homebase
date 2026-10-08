from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator, URLValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


def default_currency():
    return settings.HOMEBASE_DEFAULT_CURRENCY


class Provider(models.Model):
    """A company homebase tracks things at, such as GoDaddy, Cloudflare, or DigitalOcean."""

    class Integration(models.TextChoices):
        CLOUDFLARE = "cloudflare", "Cloudflare"
        GODADDY = "godaddy", "GoDaddy"
        DIGITALOCEAN = "digitalocean", "DigitalOcean"
        HOSTINGER = "hostinger", "Hostinger"

    name = models.CharField(max_length=100, unique=True)
    website = models.URLField(blank=True)
    account_notes = models.TextField(
        blank=True,
        help_text="Account name, login email, support PIN location. Never store passwords here.",
    )
    integration = models.CharField(
        max_length=20,
        choices=Integration,
        blank=True,
        help_text="The provider API homebase reads live data from, when its token is configured.",
    )

    # Written by the provider sync (see inventory/providers).
    sync_attempted_at = models.DateTimeField(null=True, editable=False)
    sync_succeeded_at = models.DateTimeField(null=True, editable=False)
    sync_error = models.TextField(blank=True, editable=False)
    sync_summary = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["integration"],
                condition=~Q(integration=""),
                name="unique_provider_integration",
            )
        ]

    def __str__(self):
        return self.name


class Domain(models.Model):
    name = models.CharField(max_length=253, unique=True)
    registrar = models.ForeignKey(
        Provider, on_delete=models.PROTECT, related_name="registered_domains"
    )
    dns_provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        related_name="dns_domains",
        null=True,
        blank=True,
        help_text="Leave empty when DNS is hosted at the registrar.",
    )
    expires_on = models.DateField()
    auto_renew = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    # Written by the provider sync. "missing" means the provider no longer reports it.
    registrar_status = models.CharField(max_length=50, blank=True, editable=False)
    dns_zone_status = models.CharField("DNS zone status", max_length=50, blank=True, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = self.name.strip().lower()
        super().save(*args, **kwargs)

    def clean(self):
        self.name = self.name.strip().lower()


class Server(models.Model):
    class Kind(models.TextChoices):
        HOME = "home", "Home server"
        VPS = "vps", "VPS"
        SHARED = "shared", "Shared hosting"

    name = models.CharField(max_length=100, unique=True)
    provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        related_name="servers",
        null=True,
        blank=True,
        help_text="Leave empty for hardware you own, such as the home server.",
    )
    kind = models.CharField(max_length=10, choices=Kind)
    host = models.CharField("public IP or hostname", max_length=253, blank=True)
    location = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    external_id = models.CharField(
        "provider ID",
        max_length=100,
        blank=True,
        help_text="The droplet or VM ID at the provider. Set by the provider sync; "
        "clear it to unlink.",
    )

    # Written by the provider sync. "missing" means the provider no longer reports it.
    live_status = models.CharField(max_length=30, blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                condition=~Q(external_id=""),
                name="unique_server_external_id",
            )
        ]

    def __str__(self):
        return self.name


class Subscription(models.Model):
    class BillingCycle(models.IntegerChoices):
        MONTHLY = 1, "Monthly"
        QUARTERLY = 3, "Quarterly"
        SEMIANNUAL = 6, "Every 6 months"
        YEARLY = 12, "Yearly"
        BIENNIAL = 24, "Every 2 years"
        TRIENNIAL = 36, "Every 3 years"
        QUADRENNIAL = 48, "Every 4 years"

    name = models.CharField(max_length=150)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="subscriptions")
    cost = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    currency = models.CharField(
        max_length=3,
        default=default_currency,
        validators=[RegexValidator(r"^[A-Z]{3}$", "Use a three-letter ISO code such as USD.")],
    )
    billing_cycle = models.PositiveSmallIntegerField(
        choices=BillingCycle, default=BillingCycle.YEARLY
    )
    next_renewal = models.DateField()
    auto_renew = models.BooleanField(default=False)
    active = models.BooleanField(
        default=True, help_text="Untick for cancelled subscriptions to keep them as history."
    )
    domain = models.ForeignKey(
        Domain,
        on_delete=models.SET_NULL,
        related_name="subscriptions",
        null=True,
        blank=True,
        help_text="Set when this subscription is a domain registration.",
    )
    server = models.ForeignKey(
        Server,
        on_delete=models.SET_NULL,
        related_name="subscriptions",
        null=True,
        blank=True,
        help_text="Set when this subscription pays for a server plan.",
    )
    notes = models.TextField(blank=True)
    external_id = models.CharField(
        "provider ID",
        max_length=100,
        blank=True,
        help_text="The billing ID at the provider. Set by the provider sync, which then keeps "
        "cost, renewal date, and auto-renew up to date. Clear it to unlink.",
    )

    class Meta:
        ordering = ["next_renewal", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                condition=~Q(external_id=""),
                name="unique_subscription_external_id",
            )
        ]

    def __str__(self):
        return self.name

    @property
    def monthly_cost(self):
        return self.cost / self.billing_cycle

    @property
    def yearly_cost(self):
        return self.cost * 12 / self.billing_cycle


class App(models.Model):
    class Status(models.TextChoices):
        LIVE = "live", "Live"
        DEVELOPMENT = "development", "In development"
        PAUSED = "paused", "Paused"
        RETIRED = "retired", "Retired"

    name = models.CharField(max_length=100, unique=True)
    hostname = models.CharField(
        "subdomain or hostname", max_length=253, blank=True, help_text="For example app.example.com"
    )
    server = models.ForeignKey(
        Server, on_delete=models.SET_NULL, related_name="apps", null=True, blank=True
    )
    url = models.URLField(
        "URL",
        blank=True,
        help_text="Checked by the reachability monitor.",
        validators=[URLValidator(schemes=["http", "https"])],
    )
    repo_url = models.URLField("repository URL", blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.LIVE)
    notes = models.TextField(blank=True)

    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_check_ok = models.BooleanField(null=True, editable=False)
    last_check_status_code = models.PositiveSmallIntegerField(null=True, editable=False)
    last_check_response_ms = models.PositiveIntegerField(null=True, editable=False)
    last_check_error = models.CharField(max_length=255, blank=True, editable=False)
    last_check_tls_expires_at = models.DateTimeField(
        "TLS certificate expires", null=True, editable=False
    )
    down_since = models.DateTimeField(null=True, editable=False)
    monitoring_since = models.DateTimeField(null=True, editable=False)

    # Everything the monitor learned about the current URL, and their cleared values.
    CHECK_STATE = {
        "last_checked_at": None,
        "last_check_ok": None,
        "last_check_status_code": None,
        "last_check_response_ms": None,
        "last_check_error": "",
        "last_check_tls_expires_at": None,
        "down_since": None,
    }

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Reachability belongs to the URL that was checked. Writing a different
        # URL, or clearing it, drops the previous result. Saves that omit `url` do not.
        update_fields = kwargs.get("update_fields")
        url_is_saved = update_fields is None or "url" in update_fields
        if self.pk and url_is_saved:
            previous_url = (
                type(self).objects.filter(pk=self.pk).values_list("url", flat=True).first()
            )
            if previous_url is not None and previous_url != self.url:
                self.monitoring_since = timezone.now()
                for field, cleared in self.CHECK_STATE.items():
                    setattr(self, field, cleared)
                if update_fields is not None:
                    kwargs["update_fields"] = {
                        *update_fields,
                        *self.CHECK_STATE,
                        "monitoring_since",
                    }
        super().save(*args, **kwargs)

    @classmethod
    def checkable(cls):
        """Apps the reachability monitor should probe."""
        return cls.objects.exclude(url="").exclude(status=cls.Status.RETIRED)


class AppCheck(models.Model):
    """One reachability check of an app, kept as uptime history."""

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="checks")
    url = models.CharField("URL", max_length=200)
    # Indexed on its own too: the stalled-monitor check and the cleanup filter only by time.
    checked_at = models.DateTimeField(db_index=True)
    ok = models.BooleanField()
    status_code = models.PositiveSmallIntegerField(null=True)
    response_ms = models.PositiveIntegerField(null=True)
    error = models.CharField(max_length=255, blank=True)
    tls_expires_at = models.DateTimeField("TLS certificate expires", null=True)

    class Meta:
        ordering = ["-checked_at"]
        indexes = [models.Index(fields=["app", "checked_at"])]

    def __str__(self):
        return f"{self.app} at {self.checked_at:%Y-%m-%d %H:%M}"


class DnsRecord(models.Model):
    """A DNS record of a domain, as the DNS provider reported it on the last sync."""

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="dns_records")
    external_id = models.CharField(max_length=64)
    record_type = models.CharField("type", max_length=10)
    name = models.CharField(max_length=253)
    content = models.TextField()
    proxied = models.BooleanField(null=True)
    ttl = models.PositiveIntegerField("TTL", help_text="1 means automatic.")

    class Meta:
        ordering = ["domain", "name", "record_type", "content"]
        constraints = [
            models.UniqueConstraint(
                fields=["domain", "external_id"], name="unique_dns_record_external_id"
            )
        ]
        verbose_name = "DNS record"

    def __str__(self):
        return f"{self.name} {self.record_type} {self.content}"


class Tunnel(models.Model):
    """A Cloudflare Tunnel, as Cloudflare reported it on the last sync."""

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="tunnels")
    external_id = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=20)
    status_changed_at = models.DateTimeField(null=True)
    server = models.ForeignKey(
        Server,
        on_delete=models.SET_NULL,
        related_name="tunnels",
        null=True,
        blank=True,
        help_text="The machine that runs the connector.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"], name="unique_tunnel_external_id"
            )
        ]

    def __str__(self):
        return self.name


class Alert(models.Model):
    """An alert raised by `update_alerts`, kept until resolved for email de-duplication."""

    class Kind(models.TextChoices):
        DOMAIN_EXPIRY = "domain_expiry", "Domain expiry"
        SUBSCRIPTION_RENEWAL = "subscription_renewal", "Subscription renewal"
        TLS_EXPIRY = "tls_expiry", "TLS certificate expiry"
        DOWNTIME = "downtime", "Downtime"
        SYNC_FAILURE = "sync_failure", "Provider sync failure"

    class Severity(models.TextChoices):
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    key = models.CharField(max_length=100)
    kind = models.CharField(max_length=30, choices=Kind)
    severity = models.CharField(max_length=10, choices=Severity)
    title = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True)
    raised_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    emailed_severity = models.CharField(
        max_length=10,
        choices=Severity,
        blank=True,
        help_text="The highest severity an email has gone out for.",
    )
    resolution_emailed = models.BooleanField(default=False)
    email_error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-raised_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["key"], condition=Q(resolved_at=None), name="unique_open_alert_key"
            )
        ]

    def __str__(self):
        return self.title
