FROM python:3.12-slim

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install dependencies first (layer-cached unless pyproject.toml changes)
COPY pyproject.toml README.md ./
RUN uv pip install --system .

# Copy application source
COPY app/ ./app/

# Data directory for SQLite (mount a volume here for persistence)
RUN mkdir -p /data
ENV CK_DB_PATH=/data/chronicle_keeper_sync.db

ENV CK_HOST=0.0.0.0
ENV CK_PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host $CK_HOST --port $CK_PORT"]
