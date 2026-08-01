#!/usr/bin/env bash
#
# Build, run, inspect and publish the Docker deployment.
#
# Usage:
#   scripts/deploy.sh build [tag]
#   scripts/deploy.sh up [service] [--pull] [--foreground]
#   scripts/deploy.sh down [--volumes]
#   scripts/deploy.sh logs [service] [--tail N] [--no-follow]
#   scripts/deploy.sh push <registry/image:tag>

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

deploy_build() {

case "${1:-}" in
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_cmd docker

IMAGE="${1:-${IMAGE_NAME:-korailbot:local}}"

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

info "Building ${IMAGE}"
docker build -t "$IMAGE" .
ok "Built ${IMAGE}"

docker image inspect "$IMAGE" --format '  size: {{.Size}} bytes'

}

deploy_up() {

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

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
require_env_file

# docker-compose.yml refuses to start Redis without a password.
if [[ -z "$(env_value REDIS_PASSWORD)" ]]; then
    die "REDIS_PASSWORD is empty in .env. Run 'scripts/setup.sh secrets'."
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
    info "Follow the logs with: scripts/deploy.sh logs"
else
    info "Starting services in the foreground (Ctrl-C to stop)"
    compose up ${SERVICES[@]+"${SERVICES[@]}"}
fi

}

deploy_down() {

require_cmd docker

REMOVE_VOLUMES=0
for arg in "$@"; do
    case "$arg" in
        --volumes|-v) REMOVE_VOLUMES=1 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

if [[ "$REMOVE_VOLUMES" -eq 1 ]]; then
    warn "This deletes the Redis volume: all sessions and reservation state go away."
    read -r -p "Type 'yes' to continue: " confirmation
    [[ "$confirmation" == "yes" ]] || die "Aborted."
    info "Stopping services and removing volumes"
    compose down --volumes
    ok "Services stopped, volumes removed"
else
    info "Stopping services"
    compose down
    ok "Services stopped (data volume kept)"
fi

}

deploy_logs() {

require_cmd docker

FOLLOW=1
TAIL=100
SERVICES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tail) TAIL="${2:?--tail needs a number}"; shift 2 ;;
        --no-follow) FOLLOW=0; shift ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) SERVICES+=("$1"); shift ;;
    esac
done

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

ARGS=(logs --tail "$TAIL")
[[ "$FOLLOW" -eq 1 ]] && ARGS+=(--follow)

compose "${ARGS[@]}" ${SERVICES[@]+"${SERVICES[@]}"}

}

deploy_push() {

case "${1:-}" in
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_cmd docker

IMAGE="${1:-${IMAGE_NAME:-}}"
[[ -n "$IMAGE" ]] || die "No image tag. Pass one, or set IMAGE_NAME (e.g. you/korailbot:latest)."

# A bare name with no registry namespace only exists on this machine; pushing
# it would either fail or, worse, land somewhere unintended.
[[ "$IMAGE" == */* ]] || die "'${IMAGE}' has no registry namespace - use something like you/korailbot:latest."

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

warn "About to publish ${IMAGE}."
warn "Anything pulling this tag will run the new image."
read -r -p "Type 'yes' to continue: " confirmation
[[ "$confirmation" == "yes" ]] || die "Aborted."

info "Building ${IMAGE}"
docker build -t "$IMAGE" .

info "Pushing ${IMAGE}"
docker push "$IMAGE"

ok "Published ${IMAGE}"

}

COMMAND="${1:-}"
[[ -n "$COMMAND" ]] || {
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}
shift

case "$COMMAND" in
    build) deploy_build "$@" ;;
    up) deploy_up "$@" ;;
    down) deploy_down "$@" ;;
    logs) deploy_logs "$@" ;;
    push|publish) deploy_push "$@" ;;
    -h|--help|help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) die "Unknown deploy command: $COMMAND" ;;
esac
