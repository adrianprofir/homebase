# Deploying on a home server: a worked example

This page walks through one complete, LAN-first deployment of homebase: a small Linux box at home, the dashboard reachable from the home network and, optionally, over Tailscale, with nightly database backups.
It is an example to adapt, not the only way to run homebase.
The [README](../README.md) has the generic five-minute quickstart.

homebase is built for trusted networks.
The Compose stack publishes only the dashboard port, on the address in `HOMEBASE_BIND_ADDRESS` and, with the home-server override, on the Tailscale address in `HOMEBASE_TAILSCALE_ADDRESS`.
Postgres publishes no port at all.
Keep the bind address a LAN address (or `127.0.0.1`), and do not forward the port on the router.
Docker port bindings bypass host firewalls such as ufw, so binding to `0.0.0.0` on a machine with a public IP would expose the dashboard to the internet.
The dashboard is served over plain HTTP, which is acceptable only on a trusted home network.
To put it behind a reverse proxy with TLS, terminate TLS in the proxy and set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for the public name.

Requirements: Docker with the Compose plugin.
The steps below use these stand-in values, which you replace with your own:

| Stand-in | Meaning |
| --- | --- |
| `192.0.2.10` | LAN address of the server (an address from the RFC 5737 documentation range) |
| `<tailscale-address>` | Tailscale address of the server, only if you use the override |
| `<server>.<tailnet>.ts.net` | MagicDNS name of the server, only if you use the override |
| `server.example` | SSH name of the server |
| `/srv/homebase` | Directory the stack lives in |

## Install

1. Copy the code to the server.
   The server does not need GitHub access: export the tree from a checkout on your workstation and record which commit it is.

   ```sh
   git fetch origin
   git archive origin/main | ssh server.example 'mkdir -p /srv/homebase && tar -x -C /srv/homebase'
   git rev-parse origin/main | ssh server.example 'cat > /srv/homebase/DEPLOYED_COMMIT'
   ```

   Cloning the repository on the server works just as well.

2. Create `.env` and `docker-compose.override.yml` on the server:

   ```sh
   cd /srv/homebase
   cp .env.example .env
   chmod 600 .env
   cp deploy/docker-compose.override.example.yml docker-compose.override.yml
   ```

   Then edit `.env`:

   ```sh
   SECRET_KEY=<generated secret>
   DEBUG=false
   ALLOWED_HOSTS=192.0.2.10,<tailscale-address>,<server>.<tailnet>.ts.net
   CSRF_TRUSTED_ORIGINS=http://192.0.2.10:8090,http://<tailscale-address>:8090,http://<server>.<tailnet>.ts.net:8090
   TIME_ZONE=Europe/Copenhagen
   POSTGRES_PASSWORD=<output of: openssl rand -hex 32>
   HOMEBASE_BIND_ADDRESS=192.0.2.10
   HOMEBASE_PORT=8090
   HOMEBASE_TAILSCALE_ADDRESS=<tailscale-address>
   HOMEBASE_BACKUP_DIR=/srv/backups/homebase
   ```

   Use a URL-safe Postgres password such as the hex string above, because it is embedded in a database URL.
   Pick a port that nothing else listens on (`ss -ltn`).
   `tailscale status --json` shows the MagicDNS name under `Self.DNSName`.
   Leave out the override and the two override variables if the dashboard should be reachable from the LAN only.

3. Build and start the stack.
   The `web` service applies database migrations every time it starts, before gunicorn begins serving.

   ```sh
   docker compose up -d --build
   ```

4. Create your login (once):

   ```sh
   docker compose exec web python manage.py createsuperuser
   ```

5. Schedule the nightly backup in the crontab of a user in the `docker` group (`crontab -e`):

   ```sh
   30 2 * * * /bin/bash /srv/homebase/deploy/backup.sh
   ```

6. Open `http://192.0.2.10:8090/` from the home network, or `http://<tailscale-address>:8090/` over Tailscale.

The stack runs these services:

- `db`: Postgres 18, with data in the `pgdata` named volume.
- `web`: gunicorn serving Django, with static files served by WhiteNoise, published on `HOMEBASE_BIND_ADDRESS`.
- `checker`: runs `monitor` every `CHECK_INTERVAL_SECONDS`: provider sync when due, app checks, and alerts.
- `tailscale` (override only): forwards `HOMEBASE_TAILSCALE_ADDRESS` to the dashboard; see below for why it exists.

## Why Tailscale goes through a forwarder

Publishing the dashboard on a Tailscale address as a second port binding on `web` looks simpler, but it has a failure mode at boot.
A direct publish takes the LAN dashboard down whenever the server boots before Tailscale has its address.
This was tested with Docker 29.8, simulating a reboot by restarting a Docker-in-Docker daemon while the Tailscale-like address was missing:

| After a boot without the Tailscale address | Direct second port on `web` | `tailscale` forwarder |
| --- | --- | --- |
| `web` container | Exited: `failed to bind host port ...: cannot assign requested address` | Running |
| Dashboard on the LAN | Down | Up |
| Once the address appears | Stays down; Docker does not retry a container that failed to start | Tailscale side up about 10 seconds later, by the restart policy |

A common workaround for the direct publish is an `@reboot` cron job that runs `docker compose up -d` after a delay, which still leaves both addresses down if Tailscale needs longer.
The forwarder keeps the LAN dashboard independent of Tailscale and recovers by itself, at the cost of one small container.
It also hides the tailnet client address from the gunicorn access log, which shows the forwarder instead.

## Updating

Copy the new tree over the old one from your workstation, then rebuild:

```sh
git fetch origin
git archive origin/main | ssh server.example 'tar -x -C /srv/homebase'
git rev-parse origin/main | ssh server.example 'cat > /srv/homebase/DEPLOYED_COMMIT'
ssh server.example 'cd /srv/homebase && docker compose up -d --build'
```

`tar` does not delete files that were removed from the repository; they are harmless, but you can delete them by hand.

## Backups

The inventory lives in the `pgdata` volume.
`deploy/backup.sh` dumps it into `HOMEBASE_BACKUP_DIR` as `homebase-YYYYMMDD-HHMMSS.dump` (pg_dump custom format), checks that the dump is readable, and deletes dumps older than 14 days (`RETENTION_DAYS`).
Each run appends to `backup.log` in the stack directory, and a failed run leaves a `BACKUP_FAILED` file there until the next successful one.
Copy the dumps off the server as part of your wider backups.

To check a dump without touching the live database, restore it into a throwaway database:

```sh
docker compose exec -T db sh -euc '
  createdb -U "$POSTGRES_USER" homebase_restorecheck
  pg_restore -U "$POSTGRES_USER" -d homebase_restorecheck --no-owner --exit-on-error /backups/homebase-YYYYMMDD-HHMMSS.dump
  psql -U "$POSTGRES_USER" -d homebase_restorecheck -c "select count(*) from auth_user"
  dropdb -U "$POSTGRES_USER" homebase_restorecheck
'
```

To restore for real, stop the app services, replace the database, then start everything again:

```sh
docker compose stop web checker
docker compose exec -T db sh -euc '
  dropdb -U "$POSTGRES_USER" "$POSTGRES_DB"
  createdb -U "$POSTGRES_USER" "$POSTGRES_DB"
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error /backups/homebase-YYYYMMDD-HHMMSS.dump
'
docker compose up -d
```
