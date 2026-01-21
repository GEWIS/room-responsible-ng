#!/bin/sh
set -e

uvicorn main:app --host 0.0.0.0 --port 8000 &
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
