#!/bin/bash
# Runs uvicorn and Caddy side by side. If either exits, stop the other and exit
# with its status, so the container stops (and restarts) instead of Caddy
# serving 502s for a dead backend.

uvicorn main:app --host 0.0.0.0 --port 8000 &
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &

trap 'kill -TERM $(jobs -p) 2>/dev/null' TERM INT

wait -n
status=$?
kill -TERM $(jobs -p) 2>/dev/null
wait
exit "$status"
