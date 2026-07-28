#!/usr/bin/env bash
#
# Open a redis-cli session against the running Redis container, authenticated
# with REDIS_PASSWORD from .env.
#
# Usage:
#   scripts/redis-cli.sh                    # interactive shell
#   scripts/redis-cli.sh KEYS 'user_session:*'
#   scripts/redis-cli.sh --keys             # summarise the stored key space
#
# Sessions contain personal data; credentials in them are encrypted and will
# look like 'v1:gAAAAA...'. Seeing plaintext there means encryption is off.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_cmd docker

cd "$ROOT_DIR"
require_env_file

PASSWORD="$(env_value REDIS_PASSWORD)"
[[ -n "$PASSWORD" ]] || die "REDIS_PASSWORD is not set in .env"

# Either the compose stack or the standalone instance scripts/dev-redis.sh
# starts for host-side runs; whichever is up is the one to talk to.
CONTAINER=""
for candidate in korail_redis korail_dev_redis; do
    if docker ps --format '{{.Names}}' | grep -qx "$candidate"; then
        CONTAINER="$candidate"
        break
    fi
done

if [[ -z "$CONTAINER" ]]; then
    err "No Redis container is running."
    err "  compose stack:     scripts/docker-up.sh"
    die "  local development: scripts/dev-redis.sh"
fi

if [[ "${1:-}" == "--keys" ]]; then
    info "Key space summary"
    for prefix in user_session running_reservation payment_status \
                  multi_reservation_status partial_reservations \
                  admin_authenticated admin_auth_failures subscribers; do
        count="$(docker exec "$CONTAINER" redis-cli -a "$PASSWORD" --no-auth-warning \
            --scan --pattern "${prefix}*" 2>/dev/null | wc -l | tr -d ' ')"
        printf '  %-28s %s\n' "$prefix" "$count"
    done
    exit 0
fi

exec docker exec -it "$CONTAINER" redis-cli -a "$PASSWORD" --no-auth-warning "$@"
