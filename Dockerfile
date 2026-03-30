# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -r sportshub && useradd -r -g sportshub sportshub

COPY --from=builder /install /usr/local
COPY alembic.ini .
COPY alembic/ alembic/
COPY src/ src/
COPY scripts/ scripts/
COPY data/ data/

RUN chown -R sportshub:sportshub /app
USER sportshub

EXPOSE 8000

CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn sportshub.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
