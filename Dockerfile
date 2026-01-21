FROM caddy:2.8.4-alpine AS caddy

FROM python:3.11-slim

COPY --from=caddy /usr/bin/caddy /usr/bin/caddy

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/venv \
  && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

COPY backend/ /app
COPY frontend/index.html /usr/share/caddy/index.html
COPY Caddyfile /etc/caddy/Caddyfile
COPY start.sh /start.sh
RUN chmod +x /start.sh

ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 80

CMD ["/start.sh"]
