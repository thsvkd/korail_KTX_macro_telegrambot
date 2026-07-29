# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.9

# --------------------------------------------------------------------- build
FROM ghcr.io/astral-sh/uv:${UV_VERSION}-python${PYTHON_VERSION}-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# korail2 is installed from a git fork, which uv fetches with a git client.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Third-party dependencies on their own layer, so editing application code
# does not invalidate them. Bind-mounted rather than copied: the manifests
# are only needed for the duration of the command.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-editable --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# --no-editable copies the package into site-packages instead of linking back
# to /app/src, so the runtime stage needs nothing but the virtualenv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ------------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

# The bot has no reason to run as root, and anything that escapes the
# application starts from a lower base if it does not.
RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --no-create-home app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app
EXPOSE 8080

# waitress rather than the Flask development server, and deliberately a
# single process: korail_bot.app starts the Telegram poller and the registry
# of running search processes at import time, so a forking server with
# several workers would give the bot token competing getUpdates consumers
# (Telegram answers the second one with a 409) and each worker would try to
# reconcile the same searches. Concurrency comes from threads instead.
#
# `exec` hands PID 1 to waitress, so SIGTERM from `docker stop` reaches the
# shutdown handling in korail_bot.app rather than a shell that ignores it.
CMD ["sh", "-c", \
     "exec waitress-serve \
        --listen=${FLASK_HOST:-0.0.0.0}:${FLASK_PORT:-8080} \
        --threads=${WAITRESS_THREADS:-8} \
        korail_bot.app:application"]
