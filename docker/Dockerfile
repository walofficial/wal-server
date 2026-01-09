FROM ghcr.io/astral-sh/uv:python3.13-bookworm

# Install ffmpeg and optimize for parallel processing
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

ENV UV_LINK_MODE=copy

# Set environment variables for better async performance
ENV PYTHONUNBUFFERED=1
ENV ASYNCIO_DEBUG=0

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

ADD . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="$PYTHONPATH:/app/src"

ENTRYPOINT []

CMD if [ "$ENV" = "dev" ]; then \
    uvicorn ment_api.app:app \
    --host 0.0.0.0 \
    --port $PORT \
    --reload; \
    else \
    gunicorn \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --threads 8 \
    --timeout 0 \
    --access-logfile - \
    --error-logfile - \
    ment_api.app:app; \
    fi
