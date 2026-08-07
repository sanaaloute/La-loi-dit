# syntax=docker/dockerfile:1

# --- deps ---
FROM node:22-alpine AS deps
# npm registry is overridable at build time (slow/filtered networks can point
# to a mirror, e.g. https://registry.npmmirror.com) — compose passes
# NPM_REGISTRY from .env; default is the official registry.
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /app
COPY package.json package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm config set registry "$NPM_REGISTRY" && \
    npm config set fetch-timeout 600000 && \
    npm config set fetch-retries 10 && \
    npm config set fetch-retry-mintimeout 30000 && \
    npm config set fetch-retry-maxtimeout 600000 && \
    (npm ci --no-audit --no-fund || \
     (echo "npm ci failed, retrying in 5s..." && sleep 5 && npm ci --no-audit --no-fund) || \
     (echo "npm ci failed again, retrying in 5s..." && sleep 5 && npm ci --no-audit --no-fund))

# --- build ---
FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are inlined into the client bundle at build time.
ARG NEXT_PUBLIC_API_URL=""
ARG API_PROXY_TARGET="http://api:8000"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV API_PROXY_TARGET=$API_PROXY_TARGET
RUN npm run build

# --- runner (standalone output) ---
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
# Runtime target for the same-origin /backend-api rewrite (reaches the api
# service over the internal compose network). Only used when
# NEXT_PUBLIC_API_URL was left empty at build time.
ENV API_PROXY_TARGET="http://api:8000"
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
COPY --from=build /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
