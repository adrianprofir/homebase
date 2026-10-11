#!/bin/sh
# Liveness: the checker loop touches a heartbeat file after each monitor run (even on failure).
# Window matches dashboard stall tolerance (three intervals plus slack).
set -eu
heartbeat="${HOMEBASE_CHECKER_HEARTBEAT:-/tmp/heartbeat}"
max=$(( (3 * ${CHECK_INTERVAL_SECONDS:-300} + 60) / 60 + 1 ))
test -n "$(find "$heartbeat" -mmin -"$max" 2>/dev/null)"
