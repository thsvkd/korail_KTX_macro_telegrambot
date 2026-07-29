#!/usr/bin/env bash
#
# Run the bot locally against a reachable Redis.
#
# RECEIVE_MODE in .env decides how updates arrive: 'polling' (default) pulls
# them and needs no public address, 'webhook' waits for Telegram to call in.
#
# Usage:
#   scripts/run.sh              # start the Flask app
#   scripts/run.sh --debug      # start with DEBUG logging (not Flask debug)

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

for arg in "$@"; do
    case "$arg" in
        --debug) export LOG_LEVEL=DEBUG ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR"

# This script loads .env itself and adjusts some values (REDIS_HOST below).
# uv does not read .env on its own, so nothing undoes those adjustments.
load_env

# Build .venv up front rather than in the middle of the startup banner, and
# fail with something actionable instead of a traceback from app.py.
require_uv
if ! uv sync --frozen --quiet; then
    err "Could not prepare the environment from uv.lock."
    die "Run 'uv lock' if it is out of step with pyproject.toml."
fi

# Local runs talk to Redis on the host, not to the compose service name.
if [[ "${REDIS_HOST:-}" == "redis" ]]; then
    info "REDIS_HOST=redis is a compose-internal name - using localhost instead"
    export REDIS_HOST=localhost
fi

[[ -n "${BOTTOKEN:-}" ]] || die "BOTTOKEN is not set in .env"

# Mirrors the default in src/config/settings.py, which also lowercases it.
export RECEIVE_MODE="$(printf '%s' "${RECEIVE_MODE:-polling}" | tr '[:upper:]' '[:lower:]')"
case "$RECEIVE_MODE" in
    polling)
        info "Receive mode: polling (updates are pulled - no public address needed)"
        ;;
    webhook)
        info "Receive mode: webhook (Telegram must reach this host over HTTPS)"
        # Without the secret anyone who can reach /telebot could forge updates,
        # so the app refuses to start - fail here with something actionable.
        [[ -n "${TELEGRAM_WEBHOOK_SECRET:-}" ]] || \
            die "TELEGRAM_WEBHOOK_SECRET is not set. Run 'scripts/gen-secrets.sh'."
        ;;
    *)
        die "RECEIVE_MODE must be 'polling' or 'webhook' (got '${RECEIVE_MODE}')"
        ;;
esac

if [[ "${FLASK_DEBUG:-False}" =~ ^([Tt]rue|1|[Yy]es|[Oo]n)$ ]]; then
    warn "FLASK_DEBUG is enabled - the Werkzeug debugger allows remote code"
    warn "execution. Only ever do this on a machine nobody else can reach."
fi

info "Checking Redis at ${REDIS_HOST}:${REDIS_PORT:-6379}"
if ! python3 - <<'PY'
import os
import socket
import sys

host = os.environ.get("REDIS_HOST", "localhost")
port = int(os.environ.get("REDIS_PORT", "6379"))
try:
    with socket.create_connection((host, port), timeout=3):
        pass
except OSError as exc:
    print(f"{host}:{port} unreachable ({exc})", file=sys.stderr)
    sys.exit(1)
PY
then
    die "Redis is not reachable. Start one with 'scripts/dev-redis.sh'."
fi
ok "Redis reachable"

info "Starting the bot (Ctrl-C to stop)"
# The package is installed into .venv, so no PYTHONPATH is needed.
# shellcheck disable=SC2046
exec $(python_runner) -m korail_bot.app
