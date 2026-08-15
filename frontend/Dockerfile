FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# rewrites() is evaluated at BUILD time and serialized into
# .next/routes-manifest.json, so this value is baked into the image and
# changing it requires a rebuild. Without it the browser would be proxied to
# localhost:8000 from inside the container and every request would fail.
ARG API_PROXY_TARGET=http://backend:8000
ENV API_PROXY_TARGET=$API_PROXY_TARGET
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

# API_INTERNAL_URL is read at request time by server components, so it stays
# configurable without a rebuild (see src/lib/api/base-url.ts).
EXPOSE 3000
CMD ["node", "server.js"]
