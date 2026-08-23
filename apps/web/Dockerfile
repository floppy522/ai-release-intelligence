FROM node:24.19.0-alpine AS builder

WORKDIR /build
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY index.html tsconfig.json vite.config.ts eslint.config.js ./
COPY src ./src
ARG VITE_ENVIRONMENT=production
ENV VITE_ENVIRONMENT=$VITE_ENVIRONMENT
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.29.5-alpine AS runtime

COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=101:101 /build/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
CMD ["nginx", "-g", "daemon off;"]
