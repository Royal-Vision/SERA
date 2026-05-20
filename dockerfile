# ---------- Builder ----------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# cache dependency layer
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev


# ---------- Runtime ----------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup appuser

# copy installed env only
COPY --from=builder /app/.venv /app/.venv

# copy source
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 7540

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7540"]