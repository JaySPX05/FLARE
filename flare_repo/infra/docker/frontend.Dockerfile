# Dashboard: static index.html served by nginx, which also proxies
#   /api/*      -> the Risk API      (API_UPSTREAM)
#   /routing/*  -> the Routing API   (ROUTING_UPSTREAM)
# so the browser only ever talks to one origin (no CORS, one public URL).
#   docker build -f infra/docker/frontend.Dockerfile -t flare-frontend .
FROM nginxinc/nginx-unprivileged:1.27-alpine

# The image's entrypoint runs envsubst over /etc/nginx/templates/*.template at start-up
COPY infra/docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY frontend-dashboard/index.html /usr/share/nginx/html/index.html

# DNS_RESOLVER: 127.0.0.11 is Docker's own embedded DNS, correct for local
# `docker run` / docker-compose. Azure overrides this to 168.63.129.16 (Azure's
# platform DNS) via the deploy script, since Docker's address doesn't exist there.
ENV API_UPSTREAM=http://api:8000 \
    ROUTING_UPSTREAM=http://routing:8001 \
    DNS_RESOLVER=127.0.0.11 \
    NGINX_ENVSUBST_FILTER=^(API_UPSTREAM|ROUTING_UPSTREAM|DNS_RESOLVER)$

EXPOSE 8080
