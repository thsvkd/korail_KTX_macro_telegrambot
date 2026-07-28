#!/usr/bin/env bash
#
# Start the stack with docker compose.
#
# Usage:
#   scripts/docker-up.sh              # start everything in the background
#   scripts/docker-up.sh redis        # start only Redis (useful for local dev)
#   scripts/docker-up.sh --pull       # pull newer images first
#   scripts/docker-up.sh --foreground # stream logs instead of detaching

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

require_cmd docker

DETACH=1
PULL=0
SERVICES=()

for arg in "$@"; do
    case "$arg" in
        --pull) PULL=1 ;;
        --foreground|--fg) DETACH=0 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) SERVICES+=("$arg") ;;
    esac
done

cd "$ROOT_DIR"
require_env_file

# docker-compose.yml refuses to start Redis without a password.
if [[ -z "$(env_value REDIS_PASSWORD)" ]]; then
    die "REDIS_PASSWORD is empty in .env. Run 'scripts/gen-secrets.sh'."
fi

if [[ "$PULL" -eq 1 ]]; then
    info "Pulling images"
    compose pull ${SERVICES[@]+"${SERVICES[@]}"}
fi

if [[ "$DETACH" -eq 1 ]]; then
    info "Starting services"
    compose up -d ${SERVICES[@]+"${SERVICES[@]}"}
    echo
    compose ps
    echo
    info "Follow the logs with: scripts/docker-logs.sh"
else
    info "Starting services in the foreground (Ctrl-C to stop)"
    compose up ${SERVICES[@]+"${SERVICES[@]}"}
fi
