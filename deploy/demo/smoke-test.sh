#!/bin/sh
# Smoke test for a running demo stack (see docs/demo.md). Run it from this directory after
# `docker compose up --wait`. It reads the same .env as compose.yml, and uses COMPOSE
# (default "docker compose") to talk to the stack.
#
# It proves four things: the loopback port serves the demo; the stack-unique alias on the
# edge network routes to homebase and not to another container answering to web; nothing
# but the web service joined the edge network; db and checker have no route out.
set -eu

COMPOSE=${COMPOSE:-docker compose}
DOCKER=${DOCKER:-docker}
set -a
. ./.env
set +a
PORT=${DEMO_SMOKE_PORT:-8094}
MARKER="Everything here is fictional"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Requests carry the public host and X-Forwarded-Proto the way the proxy or tunnel would,
# so ALLOWED_HOSTS and the HTTPS redirect are exercised too.
fetch_alias() {
  $DOCKER run --rm --network "$DEMO_EDGE_NETWORK" curlimages/curl:8.11.1 \
    --silent --max-time 10 -H "Host: $DEMO_HOSTNAME" -H "X-Forwarded-Proto: https" "$@"
}

echo "loopback port serves the dashboard"
curl --fail --silent --max-time 10 -H "Host: $DEMO_HOSTNAME" -H "X-Forwarded-Proto: https" \
  "http://127.0.0.1:$PORT/" | grep -q "$MARKER" || fail "no demo notice on 127.0.0.1:$PORT"
curl --fail --silent --max-time 10 "http://127.0.0.1:$PORT/healthz" | grep -qx ok || fail "/healthz"

echo "the alias on the edge network routes to homebase"
for _ in 1 2 3 4 5 6 7 8; do
  fetch_alias "http://$DEMO_EDGE_ALIAS:8000/" | grep -q "$MARKER" \
    || fail "$DEMO_EDGE_ALIAS:8000 did not answer with the demo"
done

echo "the bare name web on the edge network does not route to homebase"
for _ in 1 2 3 4 5 6 7 8; do
  if fetch_alias "http://web:8000/" | grep -q "$MARKER"; then
    fail "web on the edge network reached homebase"
  fi
done

echo "only the web service joined the edge network"
joined=$($DOCKER network inspect "$DEMO_EDGE_NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}')
for service in db checker; do
  id=$($COMPOSE ps -q "$service")
  [ -n "$id" ] || fail "$service is not running"
  name=$($DOCKER inspect --format '{{.Name}}' "$id" | sed 's|^/||')
  case " $joined" in *" $name "*) fail "$service is on the edge network" ;; esac
done

echo "db and checker have no route out"
$COMPOSE exec -T checker python -c "import urllib.request; urllib.request.urlopen('http://192.0.2.1', timeout=5)" \
  >/dev/null 2>&1 && fail "checker reached the outside"
$COMPOSE exec -T checker python -c "import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)" \
  >/dev/null 2>&1 && fail "checker reached the internet"
$COMPOSE exec -T db sh -c 'wget -q -T 5 -O /dev/null http://1.1.1.1' >/dev/null 2>&1 \
  && fail "db reached the internet"

echo "the demo image has its own name"
$DOCKER image inspect showcase-homebase-app:latest >/dev/null || fail "image showcase-homebase-app:latest missing"

echo "ok"
