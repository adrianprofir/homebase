# Contributing to homebase

Thanks for helping.
Bug reports, provider integrations, documentation fixes and small improvements are all welcome.
For anything larger than a small fix, please open an issue first so we can agree on the approach.

## Development setup

Requirements: [uv](https://docs.astral.sh/uv/) and a Postgres server.
uv installs the Python version pinned in `.python-version` by itself.

1. Start Postgres, for example in Docker:

   ```sh
   docker run -d --name homebase-postgres -p 127.0.0.1:5432:5432 \
     -e POSTGRES_DB=homebase -e POSTGRES_USER=homebase -e POSTGRES_PASSWORD=homebase \
     postgres:18-alpine
   ```

2. Create `.env` from the example and set `SECRET_KEY` and `DEBUG=true`:

   ```sh
   cp .env.example .env
   ```

   The example `DATABASE_URL` already matches the container above.

3. Install dependencies, create the schema and an admin user, and start the dev server:

   ```sh
   uv sync
   uv run python manage.py migrate
   uv run python manage.py createsuperuser
   uv run python manage.py runserver
   ```

4. Open <http://127.0.0.1:8000/>, log in, and add inventory through the **Admin** link.

Run a monitoring run by hand with `uv run python manage.py monitor`, or a single step with `check_apps`, `sync_providers`, or `update_alerts`.

## Checks

The tests need the Postgres server from `DATABASE_URL`; pytest-django creates and drops a separate test database.
GitHub Actions runs these four checks, then builds the Docker image and smoke-tests the Compose stack, and scans the history for secrets with gitleaks.
Run them before opening a pull request:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
```

The tests mock every provider API, so no tokens are needed.

## Adding a provider

A provider integration is one module in `inventory/providers/` with this contract:

- `fetch(http)` takes an `httpx.Client` already configured with the base URL and token, only calls the API, and returns the data.
  It sends GET requests only: homebase never changes anything at a provider.
  Use `get_json` from `inventory/providers/base.py` so failures become a `ProviderError` with a message that is safe to show on the dashboard.
- `apply(provider, data, now)` only writes the database and returns a one-line summary.
  It must not make network calls.

`sync()` in `inventory/providers/__init__.py` runs both, applies the result in one transaction, and records success or the error on the `Provider` row.
To add one:

1. Write the module and register it in `inventory/providers/__init__.py`.
2. Add the integration choice to the `Provider` model, with a migration.
3. Add the token setting to `homebase/settings.py` and `.env.example`.
4. Test `fetch` and `apply` against canned API responses with `FakeApi` from `inventory/tests/fake_api.py`, including a failing response, and assert that only GET requests were made.
5. Document the smallest token scope the provider allows in the README under **Provider tokens**.

## Migrations

Change models in `inventory/models.py` and run `uv run python manage.py makemigrations`.
Commit the generated migration with the model change; CI fails on a missing migration.
Never edit a migration that has been released.
Data migrations must work on a database that already holds real inventory.

## Test data and screenshots

Tests, fixtures, docs and screenshots must contain only fictional data.
Use `.example` domains and addresses (`example.com`, `alice@example.com`), the RFC 5737 documentation ranges (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`), and invented names.
Never commit real hostnames, IP addresses, tokens, personal data or your own `.env`.

## Style

- Python is formatted and linted with ruff (line length 100); keep it clean.
- Prefer simple, boring code over clever code, and add an abstraction only when there is a concrete reason.
- Write Markdown with each sentence on its own line, so diffs stay readable.
- Update the README and `.env.example` when you change behaviour or add a setting.
- Commit messages: a short imperative summary line, then a body explaining why when it is not obvious.

## Pull requests

Keep a pull request to one change, fill in the template, and make sure CI is green.
By contributing you agree that your work is released under the [MIT license](LICENSE).
