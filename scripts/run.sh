#!/usr/bin/env bash
#
# Run the bot locally against a reachable Redis.
#
# Serves with waitress, the same server the container uses, rather than the
# Flask development server: this machine runs the bot for real.
#
# Starting replaces whatever was already running. Two copies would give the
# bot token two consumers, and Telegram answers the loser of that race with a
# 409 while updates disappear into the winner.
#
# RECEIVE_MODE in .env decides how updates arrive: 'polling' (default) pulls
# them and needs no public address, 'webhook' waits for Telegram to call in.
#
# Usage:
#   scripts/run.sh              # run in the foreground (Ctrl-C to stop)
#   scripts/run.sh --daemon     # run in the background, logging to .run/
#   scripts/run.sh --stop       # stop a running bot and exit
#   scripts/run.sh --debug      # DEBUG logging (not the Flask debugger)
#
# scripts/status.sh reports on whatever is running.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

DAEMON=0
STOP_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --debug) export LOG_LEVEL=DEBUG ;;
        -d|--daemon) DAEMON=1 ;;
        --stop) STOP_ONLY=1 ;;
        -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR"
mkdir -p "$RUN_DIR"

if (( STOP_ONLY )); then
    if [[ -z "$(bot_pids)" ]]; then
        info "Nothing to stop - no bot is running"
        exit 0
    fi
    bot_stop
    exit 0
fi

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
    warn "FLASK_DEBUG only reaches the Flask development server, which this"
    warn "does not use. It is ignored here."
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

# Everything that could refuse to start has now had its say, so the running
# bot is only stopped once its replacement is known to be able to come up.
bot_stop

LISTEN="${FLASK_HOST:-0.0.0.0}:${FLASK_PORT:-8080}"
THREADS="${WAITRESS_THREADS:-8}"

# waitress-serve straight out of .venv rather than through 'uv run', so the
# bot is one process instead of a wrapper holding a child. The pid that gets
# recorded is then the pid that has the signal handlers, and stopping it does
# not depend on anything forwarding the signal.
#
# Threads, never a second worker process: the Telegram poller starts when this
# module is imported, so a forking server would give the bot token one
# consumer per worker.
SERVER=("${ROOT_DIR}/.venv/bin/waitress-serve"
        "--listen=${LISTEN}"
        "--threads=${THREADS}"
        korail_bot.app:application)

[[ -x "${SERVER[0]}" ]] || die "waitress is missing from .venv. Run 'uv sync --frozen'."

if (( DAEMON )); then
    info "Starting in the background, ${THREADS} threads on ${LISTEN}"

    # nohup, not setsid: nohup execs, so $! is the server's own pid rather
    # than that of something that spawned it and left.
    nohup "${SERVER[@]}" >>"$LOG_FILE" 2>&1 </dev/null &
    pid=$!
    echo "$pid" > "$PID_FILE"

    # Coming up means reading .env, connecting to Redis and reconciling any
    # interrupted search, so give it a moment before believing it.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        sleep 1
        _is_bot "$pid" || break
        if grep -q 'Telegram poller started\|Serving on' "$LOG_FILE" 2>/dev/null; then
            break
        fi
    done

    if ! _is_bot "$pid"; then
        rm -f "$PID_FILE"
        err "The bot exited immediately. Last lines of ${LOG_FILE#"$ROOT_DIR"/}:"
        tail -n 20 "$LOG_FILE" >&2 || true
        die "Start failed."
    fi

    ok "Running in the background as pid ${pid}"
    info "Log:    ${LOG_FILE#"$ROOT_DIR"/}"
    info "Status: scripts/status.sh"
    info "Stop:   scripts/run.sh --stop"
    exit 0
fi

info "Starting the bot on ${LISTEN} with ${THREADS} threads (Ctrl-C to stop)"
# exec keeps this pid, so what goes in the pidfile is what ends up serving.
# It is left behind on exit; every reader checks the process before trusting
# the number, so a stale one reads as 'not running' rather than as a lie.
echo $$ > "$PID_FILE"
exec "${SERVER[@]}"
