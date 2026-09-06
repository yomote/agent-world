# syntax=docker/dockerfile:1.7
FROM node:22.23.0-bookworm-slim@sha256:d9f850096136edbc402debdd8729579a288aac64574ada0ff4db26b6ae58b0b2 AS web-build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY apps/web ./apps/web
RUN npm run build

FROM python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORLD_STATIC_DIR=/app/static
WORKDIR /app
COPY requirements-prod.txt ./
RUN python -m pip install --no-cache-dir -r requirements-prod.txt \
    && addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --no-create-home app
COPY apps/world ./world
COPY --from=web-build /src/apps/web/dist ./static
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["python", "-m", "uvicorn", "world.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
