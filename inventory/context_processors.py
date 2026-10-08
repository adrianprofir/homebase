from urllib.parse import urlsplit

from django.conf import settings


def demo(request):
    url = settings.MAIN_SITE_URL
    return {
        "demo_mode": settings.DEMO_MODE,
        "main_site_url": url,
        "main_site_name": urlsplit(url).hostname or url,
    }
