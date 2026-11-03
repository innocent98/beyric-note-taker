# Next.js front door. Three stages so the runtime image holds only the standalone server, the
# static assets and public/, never the toolchain or node_modules.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Next writes rewrites into its build output, so the FastAPI address must be known here.
ARG FASTAPI_INTERNAL_URL=http://web:8000
ENV FASTAPI_INTERNAL_URL=$FASTAPI_INTERNAL_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
# The server code stays root-owned (read-only to the `node` user); only the cache directory Next
# writes at runtime belongs to `node`.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
RUN mkdir -p .next/cache && chown node:node .next/cache
USER node
EXPOSE 3000
CMD ["node", "server.js"]
