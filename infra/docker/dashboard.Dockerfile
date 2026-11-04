# Build from repo root: docker build -f infra/docker/dashboard.Dockerfile .
# Requires `output: "standalone"` in apps/dashboard/next.config.*
FROM node:26-alpine AS deps
WORKDIR /app
COPY apps/dashboard/package.json apps/dashboard/package-lock.json ./
RUN npm ci

FROM node:26-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
COPY --from=deps /app/node_modules ./node_modules
COPY apps/dashboard/ ./
RUN npm run build

FROM node:26-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S -g 10001 callshield && adduser -S -u 10001 -G callshield callshield
COPY --from=build --chown=callshield:callshield /app/.next/standalone ./
COPY --from=build --chown=callshield:callshield /app/.next/static ./.next/static
# public/ is optional; create the dir so the copy never fails
RUN mkdir -p public && chown callshield:callshield public
USER callshield
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/ >/dev/null 2>&1 || exit 1

CMD ["node", "server.js"]
