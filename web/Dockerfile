FROM nginx:1.27-alpine

# The official image renders /etc/nginx/templates/*.template with envsubst at
# start-up, so API_UPSTREAM and MAX_UPLOAD can be changed without a rebuild.
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY index.html /usr/share/nginx/html/index.html

ENV API_UPSTREAM=http://api:8000 \
    MAX_UPLOAD=50m \
    NGINX_ENVSUBST_FILTER="^(API_UPSTREAM|MAX_UPLOAD)$"

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ > /dev/null || exit 1
