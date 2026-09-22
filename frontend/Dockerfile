# Multi-stage build: compile the React/Vite dashboard, then serve the
# static output with nginx. This container has no Python, no MT5, and no
# knowledge of trading logic - it's a static file server plus a reverse
# proxy for /api and /ws to wherever the real backend is running (see
# docker-compose.yml and the README for why the backend itself isn't
# containerized the same way).

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# VITE_API_KEY (optional) bakes the dashboard's X-API-Key value into the
# static build at compile time - only needed if the backend is deployed
# with API_KEY set (i.e. bound off loopback). Pass via --build-arg.
ARG VITE_API_KEY=""
ENV VITE_API_KEY=${VITE_API_KEY}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# .template (not directly in conf.d/) so the official image's own
# entrypoint script runs envsubst on BACKEND_HOST/BACKEND_PORT before
# nginx starts - see nginx.conf.template's own comment for why.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV BACKEND_HOST=host.docker.internal
ENV BACKEND_PORT=8000
EXPOSE 80
