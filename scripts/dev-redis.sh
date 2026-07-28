#!/usr/bin/env bash
#
# Local development Redis.
#
# docker-compose.yml deliberately keeps Redis unreachable from the host, so
# scripts/run.sh - which runs the app on the host - needs its own instance.
# This one is published on 127.0.0.1 only and uses REDIS_PASSWORD from .env.
#
# Usage:
#   scripts/dev-redis.sh              # start it (no-op when already running)
#   scripts/dev-redis.sh stop         # stop and remove it
#   scripts/dev-redis.sh status       # show whether it is running

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

require_cmd docker

CONTAINER="korail_dev_redis"
ACTION="${1:-start}"

cd "$ROOT_DIR"

is_running() {
    docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"
}

case "$ACTION" in
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;

    status)
        if is_running; then
            ok "${CONTAINER} is running on 127.0.0.1:6379"
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
        [[ -n "$PASSWORD" ]] || die "REDIS_PASSWORD is not set in .env. Run 'scripts/gen-secrets.sh'."

        if is_running; then
            ok "${CONTAINER} is already running on 127.0.0.1:6379"
            exit 0
        fi

        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

        info "Starting ${CONTAINER} on 127.0.0.1:6379"
        docker run -d \
            --name "$CONTAINER" \
            -p 127.0.0.1:6379:6379 \
            redis:7-alpine \
            redis-server --requirepass "$PASSWORD" >/dev/null

        for _ in $(seq 1 30); do
            if docker exec "$CONTAINER" redis-cli -a "$PASSWORD" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
                ok "Ready. Start the bot with: scripts/run.sh"
                exit 0
            fi
            sleep 0.5
        done

        die "Redis did not become ready. Check 'docker logs ${CONTAINER}'."
        ;;

    *)
        die "Unknown action: ${ACTION}. Use start, stop or status."
        ;;
esac
