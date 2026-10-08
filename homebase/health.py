"""`/healthz`: liveness plus a database check, for container and proxy healthchecks."""

import logging

from django.db import connection
from django.http import HttpResponse

logger = logging.getLogger("homebase.health")

PATH = "/healthz"


class HealthCheckMiddleware:
    """Answer `/healthz` before any other middleware runs.

    A healthcheck inside the container calls http://127.0.0.1:8000/healthz. Answering it
    here means it works whatever ALLOWED_HOSTS lists and with SECURE_SSL_REDIRECT on.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == PATH:
            return healthz()
        return self.get_response(request)


def healthz():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logger.exception("health check failed: the database is unreachable")
        response = HttpResponse("database unavailable\n", status=503, content_type="text/plain")
    else:
        response = HttpResponse("ok\n", content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response
