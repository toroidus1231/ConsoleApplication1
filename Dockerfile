# Platform container — Python 3.12 slim with backend + frontend static assets.
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

# Frontend build is copied in by CI (or mounted in dev). Keep the mount point.
RUN mkdir -p /app/frontend/build /app/data/wal

EXPOSE 8080

CMD ["python", "-m", "src.main"]
