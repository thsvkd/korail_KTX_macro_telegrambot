#!/usr/bin/env bash
#
# Run the bot locally against a reachable Redis.
#
# Usage:
#   scripts/run.sh              # start the Flask app
#   scripts/run.sh --debug      # start with DEBUG logging (not Flask debug)

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

for arg in "$@"; do
    case "$arg" in
        --debug) export LOG_LEVEL=DEBUG ;;
        -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR"
load_env

# Local runs talk to Redis on the host, not to the compose service name.
if [[ "${REDIS_HOST:-}" == "redis" ]]; then
    info "REDIS_HOST=redis is a compose-internal name - using localhost instead"
    export REDIS_HOST=localhost
fi

[[ -n "${BOTTOKEN:-}" ]] || die "BOTTOKEN is not set in .env"
[[ -n "${TELEGRAM_WEBHOOK_SECRET:-}" ]] || \
    die "TELEGRAM_WEBHOOK_SECRET is not set. Run 'scripts/gen-secrets.sh'."

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
    die "Redis is not reachable. Start one with 'scripts/docker-up.sh redis'."
fi
ok "Redis reachable"

export PYTHONPATH="${ROOT_DIR}/src:${PYTHONPATH:-}"

info "Starting the bot (Ctrl-C to stop)"
exec $(python_runner) "${ROOT_DIR}/src/app.py"
