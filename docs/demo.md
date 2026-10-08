# Running a public demo

This page explains how to host homebase as a public, read-only demo on fictional data, behind a reverse proxy or a tunnel.
The kit lives in `deploy/demo/`.
It is separate from the main `docker-compose.yml`, which is meant for your own inventory on a trusted network.

## What the demo is

- With `DEMO_MODE=true`, visitors see the dashboard and app pages without logging in, and nothing they do can change data.
- The data comes from `manage.py seed_demo`: a fictional home lab on reserved `.example` domains and documentation IP addresses, with every date relative to today.
- App checks are simulated and provider syncs read canned responses, so the demo makes no outbound request.
- homebase refuses to start in demo mode while any provider token or `EMAIL_HOST` is set, so the demo can never read a real account or send mail.
- Links into the Django admin render as plain text.
  Keep `/admin/` private, for example behind your access proxy, or leave it unrouted.

## The stack

`deploy/demo/compose.yml` runs the Compose project `showcase-homebase` with three services:

| Service | Role | Networks |
| --- | --- | --- |
| `homebase-web` | gunicorn. Migrates and runs `seed_demo --if-empty` on start. | `backend` and the external edge network |
| `checker` | Simulated checks every `CHECK_INTERVAL_SECONDS`, and `seed_demo --reset` once a day in the `DEMO_RESET_HOUR` hour. | `backend` |
| `db` | Postgres, in a named volume. | `backend` |

- `backend` is an internal network, so `db` and `checker` have no route to the internet.
- Only `homebase-web` joins the external edge network, under the alias in `DEMO_EDGE_ALIAS`.
- `homebase-web` also publishes `127.0.0.1:${DEMO_SMOKE_PORT}` for smoke tests from the host.
  It never binds a LAN or public address.
- The image is `showcase-homebase-app:latest` and the project is `showcase-homebase`.
  The main stack builds `homebase:latest`, so building the demo on the same machine never retags the image your own instance runs.
- Every service restarts unless stopped.
  `homebase-web` has a `/healthz` healthcheck that includes the database, `db` uses `pg_isready`, and `checker` writes a heartbeat after every run.

## Naming on a shared edge network

A container on several Docker networks resolves names on all of them, and Compose registers a service's own name as an alias on every network the service joins.
If several stacks share an edge network, a service called `web` on it would make this demo answer to `web` too, and requests meant for another stack's `web` would sometimes land here.
So:

- the service that joins the edge network is called `homebase-web`, never `web`;
- the proxy or tunnel reaches the demo by `DEMO_EDGE_ALIAS`, never by a bare name such as `web` or `db`;
- CI starts the stack next to a decoy container answering to `web` on the edge network and checks that the alias reaches homebase, `web` does not, and `db` and `checker` are not on the edge network at all (`deploy/demo/smoke-test.sh`).

## Setup

You need Docker with the Compose plugin and a reverse proxy or tunnel that can reach a Docker network.

1. Create the edge network once.
   Use the name you will put in `DEMO_EDGE_NETWORK`:

   ```sh
   docker network create homebase-demo-edge
   ```

2. Configure the stack:

   ```sh
   cd deploy/demo
   cp .env.demo.example .env
   chmod 600 .env
   ```

   Set `SECRET_KEY` and `POSTGRES_PASSWORD`, and set `DEMO_HOSTNAME` to the public hostname without a scheme.
   `.env.demo.example` explains the other variables.
   It holds no provider token and no mail setting, and it must stay that way.

3. Start it:

   ```sh
   docker compose up -d --build --wait
   ```

4. Smoke test from the host.
   The loopback port serves the demo when the request carries the public host and the forwarded protocol, as your proxy would send them:

   ```sh
   curl -H "Host: $DEMO_HOSTNAME" -H "X-Forwarded-Proto: https" http://127.0.0.1:8094/
   ```

   `./smoke-test.sh` runs the full set of checks, including the alias test above.

## Putting it behind a proxy or tunnel

Point the public hostname at `http://<DEMO_EDGE_ALIAS>:8000`, from a proxy or tunnel container that is on the same edge network.
For example, with a Cloudflare Tunnel running in a container on that network, set the public hostname's service to `http://homebase-demo-web:8000`.
With a reverse proxy such as Caddy or nginx on the same network, use the same upstream.

The stack expects TLS to end at the proxy:

- the proxy forwards plain HTTP and sets `X-Forwarded-Proto: https`;
- `TRUST_X_FORWARDED_PROTO` is on, because only the proxy can reach the container;
- secure cookies, the HTTPS redirect (`/healthz` is exempt), and HSTS are on.
  Lower `SECURE_HSTS_SECONDS` while you are still testing, because browsers remember it.

If the proxy runs on the host instead of in Docker, use the loopback port as the upstream: `http://127.0.0.1:8094`.

## Operating it

- Restarting keeps the data: `seed_demo --if-empty` does nothing when the inventory has data.
- Rebuild the story by hand with `docker compose exec checker python manage.py seed_demo --reset`.
- To update, pull the new code and run `docker compose up -d --build --wait` again.
- Logs: `docker compose logs -f`.
  The checker logs every simulated run and every reseed.
- Demo data is disposable, but the Postgres volume `showcase-homebase_pgdata` is not removed by `docker compose down`.
  Add `-v` to delete it.
