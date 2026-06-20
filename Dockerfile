# ---- Frontend build stage (Modules 18-20) ----
# Compiles the React app to static assets that the platform serves at /.
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Platform container — Python 3.12 slim with backend + built frontend ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for ifcopenshell and reportlab font rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY config ./config
COPY simulator ./simulator

# Built frontend assets from the build stage; served at / by the API (Module 21).
RUN mkdir -p /app/data/wal
COPY --from=frontend-build /frontend/build /app/frontend/build

EXPOSE 8080

CMD ["python", "-m", "src.main"]
