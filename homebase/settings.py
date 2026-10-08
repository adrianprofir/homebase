"""Django settings for homebase.

Every deployment-specific value comes from the environment (or a local `.env` file).
See `.env.example` for the full list.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env.str("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "inventory",
]

MIDDLEWARE = [
    # First, so container healthchecks never depend on ALLOWED_HOSTS or the HTTPS redirect.
    "homebase.health.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "homebase.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "inventory.context_processors.demo",
            ],
        },
    },
]

WSGI_APPLICATION = "homebase.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# homebase is served over plain HTTP on the home LAN by default.
# Set these to true once it sits behind HTTPS.
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)
# Behind a reverse proxy that terminates TLS, requests reach Django over plain HTTP.
# Only trust the proxy's X-Forwarded-Proto header when clients cannot reach Django directly.
if env.bool("TRUST_X_FORWARDED_PROTO", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)

LANGUAGE_CODE = "en-us"
TIME_ZONE = env.str("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Alert email goes out over SMTP when EMAIL_HOST is set. The values are read into
# MAILERS: Django 6.1 does not allow the deprecated EMAIL_* setting names next to it.
email_host = env.str("EMAIL_HOST", default="")
if email_host:
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": {
                "host": email_host,
                "port": env.int("EMAIL_PORT", default=587),
                "username": env.str("EMAIL_HOST_USER", default=""),
                "password": env.str("EMAIL_HOST_PASSWORD", default=""),
                "use_tls": env.bool("EMAIL_USE_TLS", default=True),
                "use_ssl": env.bool("EMAIL_USE_SSL", default=False),
                "timeout": 30,
            },
        },
    }
else:
    MAILERS = {
        "default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"},
    }
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="homebase@localhost")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
    # httpx logs every request at INFO, which makes canned demo responses read as real API
    # calls. The provider sync logs one line per provider instead.
    "loggers": {"httpx": {"level": "WARNING"}},
}

# Currency pre-filled on new subscriptions (ISO 4217 code).
HOMEBASE_DEFAULT_CURRENCY = env.str("HOMEBASE_DEFAULT_CURRENCY", default="USD")
# How far ahead the dashboard looks for renewals and expiries.
HOMEBASE_RENEWAL_WINDOW_DAYS = env.int("HOMEBASE_RENEWAL_WINDOW_DAYS", default=60)
# Timeout for each request made by the `check_apps` command.
HOMEBASE_CHECK_TIMEOUT_SECONDS = env.float("HOMEBASE_CHECK_TIMEOUT_SECONDS", default=10.0)
# Seconds between monitor runs in Docker Compose. The dashboard uses it to spot a
# monitor that stopped running.
HOMEBASE_CHECK_INTERVAL_SECONDS = env.int("CHECK_INTERVAL_SECONDS", default=300)
# Days of uptime history to keep.
HOMEBASE_CHECK_HISTORY_DAYS = env.int("HOMEBASE_CHECK_HISTORY_DAYS", default=90)

# Read-only provider API tokens. Each provider is optional: without its token it is skipped.
CLOUDFLARE_API_TOKEN = env.str("CLOUDFLARE_API_TOKEN", default="")
# Only needed to list tunnels when the token sees no zones.
CLOUDFLARE_ACCOUNT_ID = env.str("CLOUDFLARE_ACCOUNT_ID", default="")
GODADDY_API_TOKEN = env.str("GODADDY_API_TOKEN", default="")
DIGITALOCEAN_API_TOKEN = env.str("DIGITALOCEAN_API_TOKEN", default="")
HOSTINGER_API_TOKEN = env.str("HOSTINGER_API_TOKEN", default="")
# Minutes between provider syncs run by `monitor`.
HOMEBASE_SYNC_INTERVAL_MINUTES = env.int("HOMEBASE_SYNC_INTERVAL_MINUTES", default=60)
# Timeout for each provider API request.
HOMEBASE_SYNC_TIMEOUT_SECONDS = env.float("HOMEBASE_SYNC_TIMEOUT_SECONDS", default=30.0)

# Alerts. Manual renewals warn this many days ahead; everything turns critical at 7 days.
HOMEBASE_EXPIRY_ALERT_DAYS = env.int("HOMEBASE_EXPIRY_ALERT_DAYS", default=30)
# TLS certificates warn this many days ahead and turn critical at 3 days.
HOMEBASE_TLS_ALERT_DAYS = env.int("HOMEBASE_TLS_ALERT_DAYS", default=10)
# A live app counts as down after failing checks for this many minutes.
HOMEBASE_DOWNTIME_ALERT_MINUTES = env.int("HOMEBASE_DOWNTIME_ALERT_MINUTES", default=10)
# Who gets alert email. Email is off unless this and EMAIL_HOST are both set.
HOMEBASE_ALERT_EMAIL_TO = env.list("HOMEBASE_ALERT_EMAIL_TO", default=[])
HOMEBASE_ALERT_EMAIL_ENABLED = bool(email_host and HOMEBASE_ALERT_EMAIL_TO)
# Dashboard address to link from alert email, for example http://192.0.2.10:8090/
HOMEBASE_DASHBOARD_URL = env.str("HOMEBASE_DASHBOARD_URL", default="")

# Public demo: the dashboard and app pages are readable without logging in, a notice says
# the data is fictional, admin links render as text, app checks are simulated, and the
# provider sync reads canned responses. homebase refuses to start in demo mode while any
# provider token or EMAIL_HOST is set, so a demo can never reach a real account or send mail.
DEMO_MODE = env.bool("DEMO_MODE", default=False)
# A site this instance belongs to, linked from a strip above the header. Empty: no strip.
MAIN_SITE_URL = env.str("MAIN_SITE_URL", default="")
