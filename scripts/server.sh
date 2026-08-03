#!/usr/bin/env bash
#
# The bot as a service on this machine: start it, stop it, look at it.
#
# By default this drives the docker compose stack - the app and its Redis, both
# in containers - which is how the bot is deployed. Telegram updates are pulled
# with long polling, so no public address is needed and neither container
# publishes a port. Redis state is bind-mounted from the host, so stopping or
# removing containers never touches registered accounts or running searches.
#
# `--host` runs the app straight out of .venv instead, against a separate
# development Redis. That is for debugging with a local interpreter; it does
# not see the compose stack's data.
#
# `--test` selects the staging bot in .env.test, which has a separate token,
# project, containers, port and Redis directory. It may stay up beside
# production without either lifecycle touching the other.
#
# Usage:
#   scripts/server.sh start [--foreground] [--build] [--debug]
#   scripts/server.sh stop [--remove]
#   scripts/server.sh restart [--build] [--debug]
#   scripts/server.sh status [--log N]
#   scripts/server.sh logs [N] [-f]
#   scripts/server.sh redis [start|stop|status]
#   scripts/server.sh redis-cli [--keys|COMMAND ...]
#
#   ... any of them with --test to act on the staging bot instead,
#       or with --host to act on a .venv process instead of the stack.
#
# `redis` manages the container the bot talks to; `redis-cli` looks inside
# whichever one it is actually using, the compose stack's included.
#
# `status` exits 0 when the bot is running and 1 when it is not, so it can
# gate something else:
#   scripts/server.sh status >/dev/null || scripts/server.sh start

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() { sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; }

TEST_RUNTIME=0
# compose unless asked otherwise: the deployed shape is the one that should be
# reachable by default, so `start` after a reboot brings up what is deployed
# rather than a second, differently-configured copy on the host.
RUNTIME_BACKEND="${RUNTIME_BACKEND:-compose}"
PRODUCTION_ENV_FILE="$ENV_FILE"
FILTERED_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --test) TEST_RUNTIME=1 ;;
        --host) RUNTIME_BACKEND="host" ;;
        --compose) RUNTIME_BACKEND="compose" ;;
        *) FILTERED_ARGS+=("$arg") ;;
    esac
done
set -- "${FILTERED_ARGS[@]}"

# on_compose - true when this invocation acts on the container stack
on_compose() { [[ "$RUNTIME_BACKEND" == "compose" ]]; }

if (( TEST_RUNTIME )); then
    use_test_runtime
else
    BOT_RUNTIME_PROFILE="production"
    export BOT_RUNTIME_PROFILE
fi

# self <subcommand...> - how to invoke this script again for the same bot.
#
# Every hint printed below has to name the right runtime, and writing that out
# by hand is how half of them ended up naming the other one.
self() {
    local suffix=""
    (( TEST_RUNTIME )) && suffix+=" --test"
    on_compose || suffix+=" --host"
    printf 'scripts/server.sh %s%s' "$*" "$suffix"
}

# ==================== Redis ====================

# Redis for the selected backend: the compose service by default, or the
# standalone development container a `--host` run needs - bound to loopback so
# nothing off the host can reach it.
server_redis() {
    local action="${1:-start}" container host_port setup_command password

    case "$action" in
        -h|--help) printf '%s\n' "Usage: $(self 'redis [start|stop|status]')"; return 0 ;;
    esac

    require_cmd docker

    if on_compose; then
        compose_redis "$action"
        return
    fi

    if (( TEST_RUNTIME )); then
        container="$(env_value DEV_REDIS_CONTAINER_NAME)"
        container="${container:-korail_test_dev_redis}"
        host_port="$(env_value DEV_REDIS_PORT)"
        host_port="${host_port:-6380}"
        setup_command="scripts/setup.sh --test"
    else
        container="korail_dev_redis"
        host_port="$(env_value REDIS_PORT)"
        host_port="${host_port:-6379}"
        setup_command="scripts/setup.sh secrets"
    fi

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

    redis_container_running() {
        docker ps --format '{{.Names}}' | grep -qx "$container"
    }

    case "$action" in
        status)
            if redis_container_running; then
                ok "${container} is running on 127.0.0.1:${host_port}"
            else
                info "${container} is not running"
            fi
            ;;

        stop)
            if redis_container_running || docker ps -a --format '{{.Names}}' | grep -qx "$container"; then
                info "Removing ${container}"
                docker rm -f "$container" >/dev/null
                ok "Stopped (data was in-memory only)"
            else
                info "${container} is not running"
            fi
            ;;

        start)
            require_env_file
            password="$(env_value REDIS_PASSWORD)"
            [[ -n "$password" ]] || die "REDIS_PASSWORD is not set in ${ENV_FILE#"$ROOT_DIR"/}. Run '${setup_command}'."

            if redis_container_running; then
                ok "${container} is already running on 127.0.0.1:${host_port}"
                return 0
            fi

            docker rm -f "$container" >/dev/null 2>&1 || true

            info "Starting ${container} on 127.0.0.1:${host_port}"
            docker run -d \
                --name "$container" \
                -p "127.0.0.1:${host_port}:6379" \
                redis:7-alpine \
                redis-server --requirepass "$password" >/dev/null

            for _ in $(seq 1 30); do
                if docker exec "$container" redis-cli -a "$password" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
                    ok "Ready. Start the bot with: $(self start)"
                    return 0
                fi
                sleep 0.5
            done

            die "Redis did not become ready. Check 'docker logs ${container}'."
            ;;

        *)
            die "Unknown redis action: ${action}. Use start, stop or status."
            ;;
    esac
}

# redis_reachable - true when something answers on the configured Redis port
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

# ==================== Compose backend ====================
#
# The deployed shape: app and Redis as containers on a private network, with
# Redis state bind-mounted from the host. Nothing here removes that directory,
# so every command below - stop, down, even removing the containers - leaves
# registered accounts and in-flight searches where they are.

# setup_hint - the setup command that fixes a missing value in this env file
setup_hint() {
    if (( TEST_RUNTIME )); then
        printf 'scripts/setup.sh --test'
    else
        printf 'scripts/setup.sh secrets'
    fi
}

# compose_preflight_redis - refuse to start a stack Redis cannot come up in
compose_preflight_redis() {
    require_cmd docker
    require_env_file
    # docker-compose.yml refuses to start Redis without a password, and does it
    # with an interpolation error rather than something a reader can act on.
    [[ -n "$(env_value REDIS_PASSWORD)" ]] || \
        die "REDIS_PASSWORD is empty in ${ENV_FILE#"$ROOT_DIR"/}. Run '$(setup_hint)'."
    ensure_redis_data_dir
}

# compose_preflight_app - everything above, plus what the bot itself needs
compose_preflight_app() {
    local token production_token

    compose_preflight_redis

    token="$(clean_default "$(env_value BOTTOKEN)")"
    [[ -n "$token" ]] || \
        die "BOTTOKEN is empty in ${ENV_FILE#"$ROOT_DIR"/}. Run '$(setup_hint)'."

    # Two stacks on one token means Telegram hands each update to whichever
    # asked first and answers the other with a 409.
    if (( TEST_RUNTIME )) && [[ -f "$PRODUCTION_ENV_FILE" ]]; then
        production_token="$(sed -n 's/^BOTTOKEN=//p' "$PRODUCTION_ENV_FILE" | tail -n 1)"
        if [[ -n "$production_token" && "$token" == "$production_token" ]]; then
            die ".env.test must use a different BOTTOKEN from .env. Create a separate bot with BotFather."
        fi
    fi
}

# compose_debug_override - raise the log level for this invocation only
#
# LOG_LEVEL reaches the container through env_file, so it cannot be overridden
# from the shell the way it can for a host process. A one-service fragment can,
# without editing .env or leaving state behind for the next start.
compose_debug_override() {
    mkdir -p "$RUN_DIR"
    COMPOSE_EXTRA_FILE="${RUN_DIR}/compose-debug.yml"
    cat > "$COMPOSE_EXTRA_FILE" <<'YAML'
services:
  app:
    environment:
      LOG_LEVEL: DEBUG
YAML
    export COMPOSE_EXTRA_FILE
}

# compose_await_app - wait for the app container to be up and past its startup
#
# Coming up means reading the configuration, connecting to Redis and
# reconciling any interrupted search, so a container that exists is not yet a
# bot that works. Returns 1 when it stopped instead.
compose_await_app() {
    local container="$1" state _
    for _ in $(seq 1 40); do
        state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo missing)"
        [[ "$state" == "running" ]] || return 1
        if compose logs --tail 200 app 2>/dev/null | \
            grep -q 'Telegram poller started\|Serving on'; then
            return 0
        fi
        sleep 1
    done
    return 0
}

# compose_redis <start|stop|status> - the stack's Redis on its own
compose_redis() {
    local action="${1:-start}" container

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    container="$(compose_container redis)"

    case "$action" in
        status)
            if container_running "$container"; then
                ok "${container} 실행 중 (컨테이너 네트워크 전용, 호스트 포트 없음)"
            else
                info "${container} 실행 중이 아님"
            fi
            info "데이터: $(redis_data_dir)"
            ;;

        stop)
            compose_preflight_redis
            if container_running "$container"; then
                info "Stopping ${container}"
                compose stop redis
                ok "Stopped. 데이터는 $(redis_data_dir) 에 그대로 있습니다."
            else
                info "${container} 실행 중이 아님"
            fi
            ;;

        start)
            compose_preflight_redis
            info "Starting ${container}"
            compose up -d --wait redis
            ok "Ready. 봇을 띄우려면: $(self start)"
            ;;

        *)
            die "Unknown redis action: ${action}. Use start, stop or status."
            ;;
    esac
}

# compose_start [--foreground] [--build] [--debug]
compose_start() {
    local foreground=0 build=0 recreate=0 arg container up=(up)

    for arg in "$@"; do
        case "$arg" in
            --foreground|--fg) foreground=1 ;;
            -d|--daemon|--detach) foreground=0 ;;
            --build) build=1 ;;
            --recreate|--force-recreate) recreate=1 ;;
            --debug) compose_debug_override ;;
            -h|--help) usage; return 0 ;;
            *) die "Unknown option: $arg" ;;
        esac
    done

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    compose_preflight_app
    container="$(compose_container app)"

    info "Runtime profile: ${BOT_RUNTIME_PROFILE} (${ENV_FILE#"$ROOT_DIR"/})"
    info "Receive mode: long polling (no public address needed)"
    info "Redis data:   $(redis_data_dir)"

    # No image and a build section means compose builds it here rather than
    # failing, so a fresh checkout needs no separate build step.
    (( build )) && up+=(--build)

    if (( foreground )); then
        info "Starting the stack in the foreground (Ctrl-C to stop)"
        compose "${up[@]}"
        return
    fi

    (( recreate )) && up+=(--force-recreate)
    up+=(-d)

    info "Starting the stack"
    compose "${up[@]}"

    if ! compose_await_app "$container"; then
        err "앱 컨테이너가 바로 종료됐습니다. 마지막 로그:"
        compose logs --tail 30 app >&2 || true
        die "Start failed."
    fi

    ok "Running as ${container}"
    info "Status: $(self status)"
    info "Logs:   $(self logs) -f"
    info "Stop:   $(self stop)"
}

# compose_stop [--remove]
#
# `stop` leaves the containers in place so the next start is a resume;
# `--remove` takes them and the network away as well. Neither touches the
# Redis directory, so the difference is only how much gets rebuilt next time.
compose_stop() {
    local remove=0 arg container

    for arg in "$@"; do
        case "$arg" in
            --remove|--down|--rm) remove=1 ;;
            -h|--help) usage; return 0 ;;
            *) die "Unknown option: $arg" ;;
        esac
    done

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    require_env_file
    container="$(compose_container app)"

    if (( remove )); then
        info "Stopping and removing the stack"
        compose down
        ok "Removed. 데이터는 $(redis_data_dir) 에 그대로 있습니다."
        return
    fi

    if ! container_exists "$container"; then
        info "Nothing to stop - the stack is not up"
        return
    fi

    info "Stopping the stack"
    compose stop
    ok "Stopped. 데이터는 $(redis_data_dir) 에 그대로 있습니다."
}

# compose_restart [--build] [--debug]
#
# Only the app is recreated. Redis is left alone unless it is down: restarting
# it would drop the app's connection for no reason, and the reason to restart
# is almost always new code or new configuration for the bot.
compose_restart() {
    local build=0 arg container recreate=(--force-recreate --no-deps)

    for arg in "$@"; do
        case "$arg" in
            --build) build=1 ;;
            --debug) compose_debug_override ;;
            --foreground|--fg) die "restart always ends detached. Use '$(self start) --foreground'." ;;
            -h|--help) usage; return 0 ;;
            *) die "Unknown option: $arg" ;;
        esac
    done

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    compose_preflight_app
    container="$(compose_container app)"

    info "Making sure Redis is up"
    compose up -d --wait redis

    (( build )) && recreate+=(--build)

    info "Recreating the app container"
    compose up -d "${recreate[@]}" app

    if ! compose_await_app "$container"; then
        err "앱 컨테이너가 바로 종료됐습니다. 마지막 로그:"
        compose logs --tail 30 app >&2 || true
        die "Restart failed."
    fi

    ok "Running as ${container}"
}

# compose_logs [N] [-f] [service]
compose_logs() {
    local lines=20 follow=0 arg services=()

    for arg in "$@"; do
        case "$arg" in
            -f|--follow) follow=1 ;;
            [0-9]*) lines="$arg" ;;
            -h|--help) usage; return 0 ;;
            app|redis) services+=("$arg") ;;
            *) die "Unknown option: $arg" ;;
        esac
    done

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    require_env_file

    local args=(logs --tail "$lines")
    (( follow )) && args+=(--follow)
    (( ${#services[@]} )) || services=(app)

    compose "${args[@]}" "${services[@]}"
}

# ==================== Start ====================

server_start() {
    local daemon=0 arg listen threads pid production_token configured_test_token

    if on_compose; then
        compose_start "$@"
        return
    fi

    warn "--host 는 .venv 프로세스와 별도 개발용 Redis를 씁니다."
    warn "배포된 스택의 등록 계정·검색 상태는 보이지 않습니다."

    for arg in "$@"; do
        case "$arg" in
            --debug) export LOG_LEVEL=DEBUG ;;
            -d|--daemon) daemon=1 ;;
            --foreground|--fg) daemon=0 ;;
            -h|--help) usage; return 0 ;;
            *) die "Unknown option: $arg" ;;
        esac
    done

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    mkdir -p "$RUN_DIR"

    # This script loads .env itself and adjusts some values (REDIS_HOST below).
    # uv does not read .env on its own, so nothing undoes those adjustments.
    load_env

    # Configuration files do not get to relabel a process into the other
    # lifecycle. The command-line profile decides which pidfile may stop it.
    if (( TEST_RUNTIME )); then
        export BOT_RUNTIME_PROFILE="test"

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
    else
        export BOT_RUNTIME_PROFILE="production"
    fi

    # Build .venv up front rather than in the middle of the startup banner, and
    # fail with something actionable instead of a traceback from app.py.
    "${SCRIPT_DIR}/bootstrap.sh" --quiet

    # Local runs talk to Redis on the host, not to the compose service name.
    if [[ "${REDIS_HOST:-}" == "redis" ]]; then
        info "REDIS_HOST=redis is a compose-internal name - using localhost instead"
        export REDIS_HOST=localhost
    fi

    if [[ -z "$(clean_default "${BOTTOKEN:-}")" ]]; then
        if (( ! TEST_RUNTIME )) && [[ -f "$TEST_ENV_FILE" ]]; then
            configured_test_token="$(sed -n 's/^BOTTOKEN=//p' "$TEST_ENV_FILE" | tail -n 1)"
            if [[ -n "$(clean_default "$configured_test_token")" ]]; then
                die "BOTTOKEN is not set in .env. To start the configured test bot, run 'scripts/server.sh start --daemon --test'."
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

    info "Checking Redis at ${REDIS_HOST}:${REDIS_PORT:-6379}"
    if ! redis_reachable; then
        case "${REDIS_HOST}" in
            localhost|127.0.0.1|::1)
                if (( TEST_RUNTIME )); then
                    info "Starting the isolated local Redis automatically"
                else
                    info "Starting the local Redis automatically"
                fi
                server_redis start
                redis_reachable || die "Redis was started but is still unreachable. Check '$(self 'redis status')'."
                ;;
            *)
                die "Redis is not reachable. Check ${REDIS_HOST}:${REDIS_PORT:-6379}."
                ;;
        esac
    fi
    ok "Redis reachable"

    # Everything that could refuse to start has now had its say, so the running
    # bot is only stopped once its replacement is known to be able to come up.
    # That is also what makes `restart` safe: a restart that cannot come back up
    # never takes the working one down.
    bot_stop

    listen="${FLASK_HOST:-127.0.0.1}:${FLASK_PORT:-8080}"
    threads="${WAITRESS_THREADS:-8}"

    # waitress-serve straight out of .venv rather than through 'uv run', so the
    # bot is one process instead of a wrapper holding a child. The pid that gets
    # recorded is then the pid that has the signal handlers, and stopping it does
    # not depend on anything forwarding the signal.
    #
    # Threads, never a second worker process: the Telegram poller starts when this
    # module is imported, so a forking server would give the bot token one
    # consumer per worker.
    local server=("${ROOT_DIR}/.venv/bin/waitress-serve"
                  "--listen=${listen}"
                  "--threads=${threads}"
                  korail_bot.app:application)

    [[ -x "${server[0]}" ]] || die "waitress is missing from .venv. Run 'scripts/bootstrap.sh'."

    if (( daemon )); then
        info "Starting in the background, ${threads} threads on ${listen}"

        # nohup, not setsid: nohup execs, so $! is the server's own pid rather
        # than that of something that spawned it and left.
        nohup "${server[@]}" >>"$LOG_FILE" 2>&1 </dev/null &
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
        info "Status: $(self status)"
        info "Stop:   $(self stop)"
        return 0
    fi

    info "Starting the bot on ${listen} with ${threads} threads (Ctrl-C to stop)"
    # exec keeps this pid, so what goes in the pidfile is what ends up serving.
    # It is left behind on exit; every reader checks the process before trusting
    # the number, so a stale one reads as 'not running' rather than as a lie.
    echo $$ > "$PID_FILE"
    exec "${server[@]}"
}

# ==================== Stop ====================

server_stop() {
    if on_compose; then
        compose_stop "$@"
        return
    fi

    case "${1:-}" in
        -h|--help) usage; return 0 ;;
        "") ;;
        *) die "Unknown option: $1" ;;
    esac

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

    if [[ -z "$(bot_pids)" ]]; then
        info "Nothing to stop - no bot is running"
        return 0
    fi
    bot_stop
}

# ==================== Restart ====================

# Ends in a daemon unless told otherwise, because a foreground restart is just
# `start`: starting already replaces whatever was running in the same profile.
server_restart() {
    local arg forwarded=(--daemon)

    if on_compose; then
        compose_restart "$@"
        return
    fi

    for arg in "$@"; do
        case "$arg" in
            --foreground|--fg) forwarded=(--foreground) ;;
            --debug) forwarded+=(--debug) ;;
            -h|--help) usage; return 0 ;;
            *) die "Unknown option: $arg" ;;
        esac
    done

    server_start "${forwarded[@]}"
}

# ==================== Logs ====================

server_logs() {
    local lines=20 follow=0 arg

    if on_compose; then
        compose_logs "$@"
        return
    fi

    for arg in "$@"; do
        case "$arg" in
            -f|--follow) follow=1 ;;
            [0-9]*) lines="$arg" ;;
            -h|--help) usage; return 0 ;;
            *) die "Unknown option: $arg" ;;
        esac
    done

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

    if [[ ! -f "$LOG_FILE" ]]; then
        # A foreground run logs to its terminal, so there is nothing to read.
        err "${LOG_FILE#"$ROOT_DIR"/} 이 없습니다."
        err "포그라운드로 실행 중이면 그 터미널에 출력되고, 아직 한 번도"
        die "데몬으로 띄운 적이 없다면 $(self 'start --daemon') 으로 시작하세요."
    fi

    if (( follow )); then
        # -F, not -f: keeps following if the file is ever replaced.
        exec tail -n "$lines" -F "$LOG_FILE"
    fi
    exec tail -n "$lines" "$LOG_FILE"
}

# ==================== Redis inspection ====================

# Reaches whichever Redis the selected bot is actually using: the compose
# stack's or the standalone one `server.sh redis` starts.
server_redis_cli() {
    local password container candidate candidates runtime_pid

    case "${1:-}" in
        -h|--help) printf '%s\n' "Usage: $(self 'redis-cli [--keys|COMMAND ...]')"; return 0 ;;
    esac

    require_cmd docker

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    load_env

    runtime_pid="$(bot_pid)"
    if [[ -n "$runtime_pid" ]]; then
        load_bot_runtime_env "$runtime_pid" || true
    fi

    password="${REDIS_PASSWORD:-}"
    [[ -n "$password" ]] || die "REDIS_PASSWORD is not set in ${ENV_FILE#"$ROOT_DIR"/}"

    if (( TEST_RUNTIME )); then
        candidates=(
            "${REDIS_CONTAINER_NAME:-korail_redis_test}"
            "${DEV_REDIS_CONTAINER_NAME:-korail_test_dev_redis}"
        )
    else
        candidates=(korail_redis korail_dev_redis)
    fi

    container=""
    for candidate in "${candidates[@]}"; do
        if docker ps --format '{{.Names}}' | grep -qx "$candidate"; then
            container="$candidate"
            break
        fi
    done

    if [[ -z "$container" ]]; then
        err "No Redis container is running."
        if (( TEST_RUNTIME )); then
            err "  compose stack:     scripts/deploy.sh --test up"
        else
            err "  compose stack:     scripts/deploy.sh up"
        fi
        die "  local development: $(self 'redis start')"
    fi

    if [[ "${1:-}" == "--keys" ]]; then
        info "Key space summary"
        local prefix count
        for prefix in user_session running_reservation payment_status \
                      multi_reservation_status partial_reservations \
                      admin_authenticated admin_auth_failures subscribers; do
            count="$(docker exec "$container" redis-cli -a "$password" --no-auth-warning \
                --scan --pattern "${prefix}*" 2>/dev/null | wc -l | tr -d ' ')"
            printf '  %-28s %s\n' "$prefix" "$count"
        done
        return 0
    fi

    # -t only when there is a terminal to allocate: with piped stdin, docker
    # refuses outright with "the input device is not a TTY", which makes this
    # script unusable from a script or a pipeline.
    local docker_flags=(-i)
    [[ -t 0 ]] && docker_flags+=(-t)

    exec docker exec "${docker_flags[@]}" "$container" \
        redis-cli -a "$password" --no-auth-warning "$@"
}
# workload_report_py - the program that prints what the bot is working on
#
# Kept as a function rather than inline so the same program can be fed to a
# host interpreter or to `compose exec` inside the app container. It has to
# run where the searches run: it reports whether each recorded search still
# has a live process, and pids only mean something in their own namespace.
workload_report_py() {
    cat <<'PY'
import os
import sys

from korail_bot.storage.redis import RedisStorage
from korail_bot.utils.privacy import mask_phone


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


storage = RedisStorage()

reservations = storage.get_all_running_reservations()
if not reservations:
    print("  검색 중인 예약 없음")
else:
    for r in reservations:
        p = r.search_params
        running = alive(r.process_id)
        mark = "\033[32m●\033[0m 검색 중" if running else "\033[31m●\033[0m 프로세스 없음"
        print(
            f"  {mark}  chat_id={r.chat_id}  "
            f"{p.rail_operator.display_name}={mask_phone(r.korail_id)}"
        )
        print(f"      {p.src_locate} → {p.dst_locate}  {p.dep_date}  "
              f"{p.dep_time[:4]}~{p.max_dep_time}  {p.train_type_display}  "
              f"{p.passenger_count}명  {p.seat_strategy}")
        print(f"      pid={r.process_id}  run_id={r.run_id or '(없음)'}")
        if not running:
            print("      ⚠ 기록만 남고 검색은 죽었습니다. 봇을 재시작하면 정리/재개합니다.")

scheduled = storage.get_all_scheduled_searches()
if scheduled:
    print()
    for s in sorted(scheduled, key=lambda s: s.start_at):
        p = s.search_params
        print(
            f"  ⏰ 예약 대기  chat_id={s.chat_id}  "
            f"{p.rail_operator.display_name}={mask_phone(s.korail_id)}"
        )
        print(f"      시작 {s.start_at:%m/%d %H:%M}  ({s.seconds_until_due() / 60:.0f}분 뒤)")
        print(f"      {p.src_locate} → {p.dst_locate}  {p.dep_date}  "
              f"{p.dep_time[:4]}~{p.max_dep_time}  {p.passenger_count}명")

payments = storage.get_all_payment_statuses()
pending = [s for s in payments if not s.completed]
if pending:
    print()
    for s in pending:
        state = "알림 동작 중" if s.reminder_active else "알림 꺼짐"
        since = s.created_at.strftime("%H:%M:%S") if s.created_at else "?"
        print(f"  💳 결제 대기  chat_id={s.chat_id}  {state}  (시작 {since})")

if not reservations and not scheduled and not pending:
    sys.exit(0)
PY
}


# ==================== Status ====================

heading() { printf '\n%s\n' "${C_BLUE}── $* ${C_RESET}"; }
field()   { printf '  %-22s %s\n' "$1" "$2"; }

# compose_status [--log N] - the same report, for the container stack
#
# Exits 1 when the bot is not running, like the host-side report, so either
# backend can gate something else.
compose_status() {
    local show_log=0 log_lines=20 app redis running=0 state status health

    while (( $# )); do
        case "$1" in
            --log) show_log=1; [[ "${2:-}" =~ ^[0-9]+$ ]] && { log_lines="$2"; shift; } ;;
            -h|--help) usage; return 0 ;;
            *) die "Unknown option: $1" ;;
        esac
        shift
    done

    require_cmd docker
    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
    require_env_file

    app="$(compose_container app)"
    redis="$(compose_container redis)"

    # -------------------- The container --------------------

    heading "봇 컨테이너"

    if ! container_exists "$app"; then
        err "컨테이너 없음"
        field "이름" "$app"
        field "기동" "$(self start)"
    else
        state="$(docker inspect -f '{{.State.Status}}' "$app" 2>/dev/null || echo unknown)"
        # 'Up 2 days (healthy)' - the same string docker ps prints, which
        # already carries uptime and health in the form people recognise.
        status="$(docker ps -a --filter "name=^${app}$" --format '{{.Status}}' 2>/dev/null || true)"
        if [[ "$state" == "running" ]]; then
            running=1
            ok "실행 중"
        else
            err "실행 중이 아님 (${state})"
        fi
        field "이름" "$app"
        field "상태" "${status:-?}"
        field "이미지" "$(docker inspect -f '{{.Config.Image}}' "$app" 2>/dev/null || echo '?')"
        field "재시작 횟수" "$(docker inspect -f '{{.RestartCount}}' "$app" 2>/dev/null || echo '?')"
        (( running )) || field "기동" "$(self start)"
    fi

    # -------------------- Configuration --------------------

    load_env

    heading "설정"
    field "런타임" "${BOT_RUNTIME_PROFILE}"
    field "설정 원본" "${ENV_FILE#"$ROOT_DIR"/} (컨테이너 env_file)"
    field "Telegram updates" "long polling"
    field "LOG_LEVEL" "${LOG_LEVEL:-INFO}"
    field "검색 간격" "${SEARCH_INTERVAL:-1}초 (지터 ${SEARCH_INTERVAL_JITTER:-0.4})"
    field "장애 알림" "연속 ${SEARCH_FAILURE_ALERT_THRESHOLD:-10}회 실패 시"
    field "Redis 데이터" "$(redis_data_dir)"

    # -------------------- Connectivity --------------------

    heading "연결"

    if container_running "$redis"; then
        health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$redis" 2>/dev/null || true)"
        ok "Redis ${redis} 실행 중${health:+ (${health})}"
    else
        err "Redis ${redis} 무응답 - '$(self 'redis start')' 로 기동"
    fi

    if (( running )); then
        health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$app" 2>/dev/null || true)"
        case "$health" in
            healthy) ok "HTTP 127.0.0.1:${FLASK_PORT:-8080} 응답 (컨테이너 내부)" ;;
            starting) info "헬스체크 시작 대기 중" ;;
            "") info "헬스체크 없음" ;;
            *) err "헬스체크 ${health} - 봇은 살아 있는데 포트가 안 열렸습니다" ;;
        esac
    fi

    # -------------------- What it is working on --------------------

    if (( running )); then
        heading "진행 중인 작업"
        # Inside the container, where the searches actually run: the report
        # checks each recorded search against the process table it belongs to.
        workload_report_py \
            | compose exec -T -e LOG_LEVEL=CRITICAL app python - \
            || warn "Redis 상태를 읽지 못했습니다"
    fi

    # -------------------- Log --------------------

    if (( show_log )); then
        heading "로그 마지막 ${log_lines}줄"
        compose logs --tail "$log_lines" --no-color app 2>/dev/null | sed 's/^/  /' \
            || warn "로그를 읽지 못했습니다"
    fi

    echo
    (( running )) || exit 1
}

server_status() {
    local show_log=0 log_lines=20 arg pid running config_source

    if on_compose; then
        compose_status "$@"
        return
    fi

    while (( $# )); do
        case "$1" in
            --log) show_log=1; [[ "${2:-}" =~ ^[0-9]+$ ]] && { log_lines="$2"; shift; } ;;
            -h|--help) usage; return 0 ;;
            *) die "Unknown option: $1" ;;
        esac
        shift
    done

    cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

    # -------------------- The process --------------------

    heading "봇 프로세스"

    pid="$(bot_pid)"
    if [[ -z "$pid" ]]; then
        err "실행 중이 아님"
        [[ -f "$PID_FILE" ]] && field "남은 pidfile" "${PID_FILE#"$ROOT_DIR"/} (죽은 프로세스를 가리킴)"
        field "기동" "$(self 'start --daemon')"
        running=0
    else
        running=1
        ok "실행 중"
        field "pid" "$pid"
        field "가동 시간" "$(bot_uptime "$pid")"
        local rss ppid cmdline listen extra
        rss="$(awk '/VmRSS/{print $2" "$3}' "/proc/${pid}/status" 2>/dev/null || true)"
        field "메모리(RSS)" "${rss:-?}"

        # Foreground and daemon runs are told apart by who the parent is: a
        # daemon was orphaned to init when the script that started it exited.
        ppid="$(awk '{print $4}' "/proc/${pid}/stat" 2>/dev/null || echo 0)"
        if [[ "$ppid" == "1" ]]; then
            field "기동 방식" "데몬 (--daemon)"
        else
            field "기동 방식" "포그라운드 (부모 pid ${ppid})"
        fi

        # || true on every one of these: grep exits 1 when it matches nothing,
        # and under `set -e` with pipefail that ends the report rather than
        # leaving a field blank.
        cmdline="$(_pid_cmdline "$pid" 2>/dev/null || true)"
        listen="$(printf '%s' "$cmdline" | grep -o -- '--listen=[^ ]*' || true)"
        [[ -n "$listen" ]] && field "listen" "${listen#--listen=}"

        extra="$(bot_pids | { grep -vx "$pid" || true; } | tr '\n' ' ')"
        if [[ -n "${extra// /}" ]]; then
            warn "봇 프로세스가 여러 개입니다: ${extra% } - 텔레그램 409 의 원인이 됩니다"
        fi
    fi

    if [[ -f "$LOG_FILE" ]]; then
        field "로그" "${LOG_FILE#"$ROOT_DIR"/} ($(du -h "$LOG_FILE" 2>/dev/null | cut -f1))"
    fi

    # -------------------- Configuration and reachability --------------------

    load_env
    config_source="${ENV_FILE#"$ROOT_DIR"/}"
    if (( TEST_RUNTIME )); then
        export BOT_RUNTIME_PROFILE="test"
        export FLASK_PORT="${FLASK_PORT:-8081}"
        export REDIS_HOST="127.0.0.1"
        export REDIS_PORT="${DEV_REDIS_PORT:-6380}"
    else
        export BOT_RUNTIME_PROFILE="production"
    fi
    if (( running )) && load_bot_runtime_env "$pid"; then
        config_source="실행 중인 pid ${pid}의 환경"
    fi
    [[ "${REDIS_HOST:-}" == "redis" ]] && export REDIS_HOST=localhost

    heading "설정"
    field "런타임" "$BOT_RUNTIME_PROFILE"
    field "설정 원본" "$config_source"
    field "Telegram updates" "long polling"
    field "LOG_LEVEL" "${LOG_LEVEL:-INFO}"
    field "검색 간격" "${SEARCH_INTERVAL:-1}초 (지터 ${SEARCH_INTERVAL_JITTER:-0.4})"
    field "장애 알림" "연속 ${SEARCH_FAILURE_ALERT_THRESHOLD:-10}회 실패 시"

    heading "연결"

    local listen_port redis_up=0
    listen_port="${FLASK_PORT:-8080}"
    if PORT="$listen_port" python3 -c '
import os, socket, sys
try:
    with socket.create_connection(("127.0.0.1", int(os.environ["PORT"])), timeout=2):
        pass
except OSError:
    sys.exit(1)
' 2>/dev/null; then
        ok "HTTP 127.0.0.1:${listen_port} 응답"
    else
        if (( running )); then
            err "HTTP 127.0.0.1:${listen_port} 무응답 - 봇은 살아 있는데 포트가 안 열렸습니다"
        else
            field "HTTP ${listen_port}" "닫힘 (봇이 꺼져 있으니 정상)"
        fi
    fi

    if REDIS_HOST="${REDIS_HOST:-localhost}" REDIS_PORT="${REDIS_PORT:-6379}" python3 -c '
import os, socket, sys
try:
    with socket.create_connection((os.environ["REDIS_HOST"], int(os.environ["REDIS_PORT"])), timeout=2):
        pass
except OSError:
    sys.exit(1)
' 2>/dev/null; then
        ok "Redis ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379} 응답"
        redis_up=1
    else
        err "Redis ${REDIS_HOST:-localhost}:${REDIS_PORT:-6379} 무응답 - '$(self 'redis start')' 로 기동"
    fi

    # -------------------- What it is working on --------------------

    if (( redis_up )) && has_venv; then
        heading "진행 중인 작업"
        # Through the project's own storage layer rather than redis-cli, so the
        # records are read with the same deserialisation the bot uses and a
        # format change cannot make this quietly wrong.
        # LOG_LEVEL=CRITICAL: opening the storage logs that it connected, which
        # belongs in the bot's log and not in the middle of this report.
        # shellcheck disable=SC2046
        workload_report_py | LOG_LEVEL=CRITICAL $(python_runner) - \
            || warn "Redis 상태를 읽지 못했습니다"
    fi

    # -------------------- Log --------------------

    if (( show_log )); then
        heading "로그 마지막 ${log_lines}줄"
        if [[ -f "$LOG_FILE" ]]; then
            tail -n "$log_lines" "$LOG_FILE" | sed 's/^/  /'
        else
            # A foreground run logs to its terminal, so there is nothing to tail.
            printf '  %s\n' "${LOG_FILE#"$ROOT_DIR"/} 이 없습니다 (포그라운드 실행은 터미널로 출력합니다)"
        fi
    fi

    echo
    (( running )) || exit 1
}

# ==================== Dispatch ====================

COMMAND="${1:-}"
[[ -n "$COMMAND" ]] || { usage; exit 0; }
shift

case "$COMMAND" in
    start) server_start "$@" ;;
    stop) server_stop "$@" ;;
    restart) server_restart "$@" ;;
    status) server_status "$@" ;;
    logs) server_logs "$@" ;;
    redis) server_redis "$@" ;;
    redis-cli) server_redis_cli "$@" ;;
    -h|--help|help) usage ;;
    *) die "Unknown command: ${COMMAND}. Try 'scripts/server.sh --help'." ;;
esac
