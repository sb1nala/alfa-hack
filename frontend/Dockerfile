# syntax=docker/dockerfile:1
# Build: docker compose build frontend. Compose passes docs/ and seed/ as the "docs" and
# "seed" build contexts: the app imports UI texts and product limits from them, and the
# build checks the API types against docs/openapi.yaml.
FROM node:24.19.0-bookworm-slim AS build
ENV CI=true COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /app/frontend
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY --from=docs openapi.yaml /app/docs/openapi.yaml
COPY --from=docs product/ui-texts.ru.json /app/docs/product/ui-texts.ru.json
COPY --from=seed products.json companies.json /app/seed/
COPY . .
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.27.5-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 8080
