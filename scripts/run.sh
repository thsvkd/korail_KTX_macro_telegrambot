#!/usr/bin/env bash
#
# Run the bot locally against a reachable Redis.
#
# Serves with waitress, the same server the container uses, rather than the
# Flask development server: this machine runs the bot for real.
#
# Starting replaces whatever was already running in the same runtime profile.
# `--test` has a separate token, pidfile, log, port and Redis, so it may stay up
# beside production without either lifecycle stopping the other.
#
# Telegram updates are pulled with long polling, so no public address is needed.
# A missing loopback Redis is started automatically in its isolated container.
#
# Usage:
#   scripts/run.sh              # run in the foreground (Ctrl-C to stop)
#   scripts/run.sh --daemon     # run in the background, logging to .run/
#   scripts/run.sh --stop       # stop a running bot and exit
#   scripts/run.sh --debug      # DEBUG logging (not the Flask debugger)
#   scripts/run.sh redis [start|stop|status]
#   scripts/run.sh --test       # use .env.test alongside the production bot
#   scripts/run.sh --test redis # manually manage the isolated test Redis
#
# scripts/status.sh reports on whatever is running.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

TEST_RUNTIME=0
PRODUCTION_ENV_FILE="$ENV_FILE"
FILTERED_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--test" ]]; then
        TEST_RUNTIME=1
    else
        FILTERED_ARGS+=("$arg")
    fi
done
set -- "${FILTERED_ARGS[@]}"

if (( TEST_RUNTIME )); then
    use_test_runtime
    STATUS_COMMAND="scripts/status.sh --test"
    STOP_COMMAND="scripts/run.sh --test --stop"
    REDIS_COMMAND="scripts/run.sh --test redis"
else
    BOT_RUNTIME_PROFILE="production"
    export BOT_RUNTIME_PROFILE
    STATUS_COMMAND="scripts/status.sh"
    STOP_COMMAND="scripts/run.sh --stop"
    REDIS_COMMAND="scripts/run.sh redis"
fi

run_redis() {

require_cmd docker

ACTION="${1:-start}"

if (( TEST_RUNTIME )); then
    CONTAINER="$(env_value DEV_REDIS_CONTAINER_NAME)"
    CONTAINER="${CONTAINER:-korail_test_dev_redis}"
    HOST_PORT="$(env_value DEV_REDIS_PORT)"
    HOST_PORT="${HOST_PORT:-6380}"
    SETUP_COMMAND="scripts/setup.sh --test"
else
    CONTAINER="korail_dev_redis"
    HOST_PORT="$(env_value REDIS_PORT)"
    HOST_PORT="${HOST_PORT:-6379}"
    SETUP_COMMAND="scripts/setup.sh secrets"
fi

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

is_running() {
    docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"
}

case "$ACTION" in
    -h|--help) printf '%s\n' 'Usage: scripts/run.sh [--test] redis [start|stop|status]'; exit 0 ;;

    status)
        if is_running; then
            ok "${CONTAINER} is running on 127.0.0.1:${HOST_PORT}"
        else
            info "${CONTAINER} is not running"
        fi
        ;;

    stop)
        if is_running || docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
            info "Removing ${CONTAINER}"
            docker rm -f "$CONTAINER" >/dev/null
            ok "Stopped (data was in-memory only)"
        else
            info "${CONTAINER} is not running"
        fi
        ;;

    start)
        require_env_file
        PASSWORD="$(env_value REDIS_PASSWORD)"
        [[ -n "$PASSWORD" ]] || die "REDIS_PASSWORD is not set in ${ENV_FILE#"$ROOT_DIR"/}. Run '${SETUP_COMMAND}'."

        if is_running; then
            ok "${CONTAINER} is already running on 127.0.0.1:${HOST_PORT}"
            return 0
        fi

        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

        info "Starting ${CONTAINER} on 127.0.0.1:${HOST_PORT}"
        docker run -d \
            --name "$CONTAINER" \
            -p "127.0.0.1:${HOST_PORT}:6379" \
            redis:7-alpine \
            redis-server --requirepass "$PASSWORD" >/dev/null

        for _ in $(seq 1 30); do
            if docker exec "$CONTAINER" redis-cli -a "$PASSWORD" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
                ok "Ready. Start the bot with: ${STOP_COMMAND% --stop}"
                return 0
            fi
            sleep 0.5
        done

        die "Redis did not become ready. Check 'docker logs ${CONTAINER}'."
        ;;

    *)
        die "Unknown action: ${ACTION}. Use start, stop or status."
        ;;
esac

}

if [[ "${1:-}" == "redis" ]]; then
    shift
    run_redis "$@"
    exit
fi


DAEMON=0
STOP_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --debug) export LOG_LEVEL=DEBUG ;;
        -d|--daemon) DAEMON=1 ;;
        --stop) STOP_ONLY=1 ;;
        -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
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

# Configuration files do not get to relabel a process into the other
# lifecycle. The command-line profile decides which pidfile may stop it.
if (( TEST_RUNTIME )); then
    export BOT_RUNTIME_PROFILE="test"
else
    export BOT_RUNTIME_PROFILE="production"
fi

if (( TEST_RUNTIME )); then
    # Host-side test and production processes can coexist only when every
    # endpoint is distinct. The test config uses its own waitress and Redis
    # ports even though long polling exposes neither app publicly.
    export FLASK_PORT="${FLASK_PORT:-8081}"
    export REDIS_HOST="127.0.0.1"
    export REDIS_PORT="${DEV_REDIS_PORT:-6380}"

    production_token=""
    if [[ -f "$PRODUCTION_ENV_FILE" ]]; then
        production_token="$(sed -n 's/^BOTTOKEN=//p' "$PRODUCTION_ENV_FILE" | tail -n 1)"
    fi
    if [[ -n "$production_token" && "${BOTTOKEN:-}" == "$production_token" ]]; then
        die ".env.test must use a different BOTTOKEN from .env. Create a separate bot with BotFather."
    fi
fi

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

if [[ -z "$(clean_default "${BOTTOKEN:-}")" ]]; then
    if (( ! TEST_RUNTIME )) && [[ -f "$TEST_ENV_FILE" ]]; then
        configured_test_token="$(sed -n 's/^BOTTOKEN=//p' "$TEST_ENV_FILE" | tail -n 1)"
        if [[ -n "$(clean_default "$configured_test_token")" ]]; then
            die "BOTTOKEN is not set in .env. To start the configured test bot, rerun with '--test --daemon'."
        fi
    fi
    die "BOTTOKEN is not set in ${ENV_FILE#"$ROOT_DIR"/}"
fi

info "Runtime profile: ${BOT_RUNTIME_PROFILE} (${ENV_FILE#"$ROOT_DIR"/})"

info "Receive mode: long polling (no public address needed)"

if [[ "${FLASK_DEBUG:-False}" =~ ^([Tt]rue|1|[Yy]es|[Oo]n)$ ]]; then
    warn "FLASK_DEBUG only reaches the Flask development server, which this"
    warn "does not use. It is ignored here."
fi

redis_reachable() {
REDIS_HOST="${REDIS_HOST:-localhost}" REDIS_PORT="${REDIS_PORT:-6379}" python3 - <<'PY'
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
}

info "Checking Redis at ${REDIS_HOST}:${REDIS_PORT:-6379}"
if ! redis_reachable; then
    case "${REDIS_HOST}" in
        localhost|127.0.0.1|::1)
            if (( TEST_RUNTIME )); then
                info "Starting the isolated local Redis automatically"
            else
                info "Starting the local Redis automatically"
            fi
            run_redis start
            redis_reachable || die "Redis was started but is still unreachable. Check '${REDIS_COMMAND} status'."
            ;;
        *)
            die "Redis is not reachable. Check ${REDIS_HOST}:${REDIS_PORT:-6379}."
            ;;
    esac
fi
ok "Redis reachable"

# Everything that could refuse to start has now had its say, so the running
# bot is only stopped once its replacement is known to be able to come up.
bot_stop

LISTEN="${FLASK_HOST:-127.0.0.1}:${FLASK_PORT:-8080}"
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
    info "Status: ${STATUS_COMMAND}"
    info "Stop:   ${STOP_COMMAND}"
    exit 0
fi

info "Starting the bot on ${LISTEN} with ${THREADS} threads (Ctrl-C to stop)"
# exec keeps this pid, so what goes in the pidfile is what ends up serving.
# It is left behind on exit; every reader checks the process before trusting
# the number, so a stale one reads as 'not running' rather than as a lie.
echo $$ > "$PID_FILE"
exec "${SERVER[@]}"
