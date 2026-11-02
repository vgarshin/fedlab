# Stage 1: build the React app into static files. Stage 2: nginx serves them and proxies /api.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/nginx/select-cert.sh deploy/nginx/entrypoint.sh /
COPY --from=web /web/dist /usr/share/nginx/html
RUN chmod +x /select-cert.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
