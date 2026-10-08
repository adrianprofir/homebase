FROM python:3.14-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN SECRET_KEY=collectstatic-only DATABASE_URL=postgres://unused/unused \
    python manage.py collectstatic --noinput

RUN useradd --system --uid 10001 --no-create-home homebase
USER homebase

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "homebase.wsgi:application"]
