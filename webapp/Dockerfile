FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY privacy.html terms.html data-deletion.html /usr/share/nginx/html/
COPY css /usr/share/nginx/html/static/css
COPY js /usr/share/nginx/html/static/js
COPY icons /usr/share/nginx/html/static/icons

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=4 \
    CMD wget -qO- http://127.0.0.1/health/live >/dev/null || exit 1
