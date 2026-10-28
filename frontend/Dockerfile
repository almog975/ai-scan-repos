FROM node:20-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm test && npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
USER 0
RUN apk upgrade --no-cache && rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
RUN chown -R 101:0 /etc/nginx && chmod -R g+w /etc/nginx
USER 101
ENV GATEWAY_UPSTREAM=gateway:8000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
CMD ["sh", "-c", "envsubst '${GATEWAY_UPSTREAM}' < /etc/nginx/nginx.conf.template > /tmp/nginx.conf && exec nginx -c /tmp/nginx.conf -g 'daemon off;'"]
