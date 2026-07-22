# Multi-stage production image for the WP5 HR web workspace (Next.js 15,
# App Router, "output: standalone"). Build context is the `frontend/`
# directory (see the `frontend` service in the repo-root docker-compose.yml).

FROM node:22-alpine AS build
# This base image's effective default user is non-root ("node", uid 1000) on
# at least some hosts/registry mirrors, which then can't write into the
# root-owned WORKDIR. Be explicit: the build stage itself is discarded (only
# its /app/.next/standalone, /app/.next/static, /app/public outputs are
# copied into the "run" stage below, which drops to the unprivileged "node"
# user), so building as root here is safe.
USER root
WORKDIR /app
COPY package.json package-lock.json ./
# node:22-alpine sets NODE_ENV=production by default, which makes plain
# `npm ci` skip devDependencies (tailwindcss, @tailwindcss/postcss, etc. —
# all required at build time for `next build`, not just for tests). Force
# dev deps in regardless of the inherited NODE_ENV.
RUN npm ci --include=dev
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# Runs as the unprivileged "node" user (uid 1000) that the official node
# image already ships with — the standalone server does not need root.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
