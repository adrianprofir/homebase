from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    Alert,
    App,
    AppCheck,
    DnsRecord,
    Domain,
    Provider,
    Server,
    Subscription,
    Tunnel,
)
from .providers.base import plural

admin.site.site_header = "homebase"
admin.site.site_title = "homebase"
admin.site.index_title = "Inventory"
admin.site.site_url = "/"


class ReadOnlyAdmin(admin.ModelAdmin):
    """For records only homebase itself writes, such as provider data and check history."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "website", "integration", "sync_succeeded_at", "sync_failing"]
    search_fields = ["name", "account_notes"]
    readonly_fields = ["sync_attempted_at", "sync_succeeded_at", "sync_summary", "sync_error"]
    fieldsets = [
        (None, {"fields": ["name", "website", "account_notes", "integration"]}),
        ("Last provider sync", {"fields": readonly_fields}),
    ]

    @admin.display(description="Sync failing", boolean=True)
    def sync_failing(self, obj):
        return bool(obj.sync_error) if obj.integration else None


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "registrar",
        "dns_provider",
        "expires_on",
        "auto_renew",
        "registrar_status",
        "dns_zone_status",
    ]
    list_filter = ["registrar", "dns_provider", "auto_renew"]
    search_fields = ["name", "notes"]
    autocomplete_fields = ["registrar", "dns_provider"]
    date_hierarchy = "expires_on"
    readonly_fields = ["registrar_status", "dns_zone_status", "dns_records_link"]
    fieldsets = [
        (
            None,
            {"fields": ["name", "registrar", "dns_provider", "expires_on", "auto_renew", "notes"]},
        ),
        ("From the provider sync", {"fields": readonly_fields}),
    ]

    @admin.display(description="DNS records")
    def dns_records_link(self, obj):
        count = obj.dns_records.count() if obj.pk else 0
        if not count:
            return "-"
        url = reverse("admin:inventory_dnsrecord_changelist") + f"?domain__id__exact={obj.pk}"
        return format_html('<a href="{}">{}</a>', url, plural(count, "record"))


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "provider",
        "price",
        "billing_cycle",
        "next_renewal",
        "auto_renew",
        "active",
        "synced",
    ]
    list_filter = ["active", "provider", "billing_cycle", "auto_renew", "currency"]
    search_fields = ["name", "notes", "external_id"]
    autocomplete_fields = ["provider", "domain", "server"]
    date_hierarchy = "next_renewal"

    @admin.display(description="Cost", ordering="cost")
    def price(self, obj):
        return f"{obj.cost} {obj.currency}"

    @admin.display(description="Synced", boolean=True)
    def synced(self, obj):
        return bool(obj.external_id)


class AppInline(admin.TabularInline):
    model = App
    fields = ["name", "hostname", "url", "status"]
    extra = 0
    show_change_link = True


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "provider", "live_status", "host", "location"]
    list_filter = ["kind", "provider"]
    search_fields = ["name", "host", "location", "notes", "external_id"]
    autocomplete_fields = ["provider"]
    readonly_fields = ["live_status"]
    inlines = [AppInline]


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ["name", "hostname", "server", "status", "last_check_ok", "last_checked_at"]
    list_filter = ["status", "server", "last_check_ok"]
    search_fields = ["name", "hostname", "url", "notes"]
    autocomplete_fields = ["server"]
    readonly_fields = [
        "uptime_link",
        "last_checked_at",
        "last_check_ok",
        "last_check_status_code",
        "last_check_response_ms",
        "last_check_error",
        "last_check_tls_expires_at",
        "down_since",
    ]
    fieldsets = [
        (None, {"fields": ["name", "hostname", "server", "url", "repo_url", "status", "notes"]}),
        ("Last reachability check", {"fields": readonly_fields}),
    ]

    @admin.display(description="Uptime history")
    def uptime_link(self, obj):
        if not obj.pk:
            return "-"
        return format_html(
            '<a href="{}">Open uptime page</a>', reverse("app_detail", args=[obj.pk])
        )


@admin.register(AppCheck)
class AppCheckAdmin(ReadOnlyAdmin):
    list_display = ["app", "checked_at", "ok", "status_code", "response_ms", "error"]
    list_filter = ["ok", "app"]
    date_hierarchy = "checked_at"
    list_select_related = ["app"]


@admin.register(DnsRecord)
class DnsRecordAdmin(ReadOnlyAdmin):
    list_display = ["name", "record_type", "content", "proxied", "ttl", "domain"]
    list_filter = ["record_type", "proxied", "domain"]
    search_fields = ["name", "content"]
    list_select_related = ["domain"]


@admin.register(Tunnel)
class TunnelAdmin(admin.ModelAdmin):
    list_display = ["name", "status", "status_changed_at", "server", "provider"]
    list_filter = ["status"]
    readonly_fields = ["provider", "external_id", "name", "status", "status_changed_at"]
    autocomplete_fields = ["server"]

    def has_add_permission(self, request):
        return False


@admin.register(Alert)
class AlertAdmin(ReadOnlyAdmin):
    list_display = ["title", "severity", "kind", "raised_at", "resolved_at", "emailed_severity"]
    list_filter = ["severity", "kind", ("resolved_at", admin.EmptyFieldListFilter)]
    search_fields = ["title"]
    date_hierarchy = "raised_at"
