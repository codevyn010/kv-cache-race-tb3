#!/bin/bash
# Supervisor loop: (re)starts the server whenever its process exits, so
# editing service/*.py and killing the running process (e.g. `pkill -f
# service.server`) is how a code change takes effect. Also bumps
# /tmp/server_generation on every (re)start so external observers can tell
# a restart happened without needing any other signal from the fix.
set -u

GEN_FILE=/tmp/server_generation
gen=0

while true; do
  gen=$((gen + 1))
  echo "$gen" > "$GEN_FILE"
  python -m service.server &
  pid=$!
  echo "$pid" > /tmp/server.pid
  wait "$pid"
  echo "server exited (generation $gen), restarting in 1s..." >&2
  sleep 1
done
