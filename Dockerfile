# syntax=docker/dockerfile:1

# Static output: build natively, not under emulation, when cross-building.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SNAPBACK_STATIC_DIR=/app/static
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/snapback ./snapback
RUN pip install --no-cache-dir . && rm -rf snapback pyproject.toml
COPY --from=frontend /src/dist /app/static
EXPOSE 8080
CMD ["python", "-m", "snapback"]
