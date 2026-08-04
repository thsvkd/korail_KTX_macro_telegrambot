#!/usr/bin/env bash
#
# Build, run, inspect and publish the Docker deployment.
#
# Usage:
#   scripts/deploy.sh [--test] build [tag]
#   scripts/deploy.sh [--test] [--publish] up [service] [--pull] [--foreground]
#   scripts/deploy.sh [--test] down [--volumes]
#   scripts/deploy.sh [--test] logs [service] [--tail N] [--no-follow]
#   scripts/deploy.sh [--test] push <registry/image:tag>
#
# --publish, -pb   Put the Mini App on the internet (Tailscale Funnel).
#                  Without it the address stays inside the tailnet, which is
#                  enough to develop against and reachable from your own
#                  phone if it is on the tailnet too.

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

TEST_STACK=0
PUBLISH=0
PRODUCTION_ENV_FILE="$ENV_FILE"
FILTERED_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --test) TEST_STACK=1 ;;
        --publish|-pb) PUBLISH=1 ;;
        *) FILTERED_ARGS+=("$arg") ;;
    esac
done
set -- "${FILTERED_ARGS[@]}"

if (( TEST_STACK )); then
    use_test_stack
fi

deploy_build() {

case "${1:-}" in
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_cmd docker

if (( TEST_STACK )) && [[ -z "${1:-}" ]]; then
    require_env_file
    IMAGE="$(env_value IMAGE_NAME)"
    IMAGE="${IMAGE:-korailbot:test}"
else
    IMAGE="${1:-${IMAGE_NAME:-korailbot:local}}"
fi

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

info "Building ${IMAGE}"
docker build -t "$IMAGE" .
ok "Built ${IMAGE}"

docker image inspect "$IMAGE" --format '  size: {{.Size}} bytes'

}

# deploy_prepare_tailscale - settle the Mini App's exposure before starting
#
# The mode is written into the env file rather than kept for this one command.
# The alternative bites: someone publishes with --publish, then restarts the
# bot a week later for an unrelated reason, and the Mini App quietly stops
# being reachable with nothing in the logs to say why. Recording it means a
# restart keeps what was chosen, and `deploy.sh up` with no flag is an
# explicit instruction to go back to tailnet-only.
deploy_prepare_tailscale() {
    local mode="serve"
    (( PUBLISH )) && mode="funnel"

    if ! tailscale_enabled; then
        if (( PUBLISH )); then
            err "--publish needs a Tailscale sidecar, and TS_AUTHKEY is empty in"
            err "  ${ENV_FILE#"$ROOT_DIR"/}."
            err "Create a key at https://login.tailscale.com/admin/settings/keys"
            err "and add it, along with the node name the URL will use:"
            err "    TS_AUTHKEY=tskey-auth-..."
            err "    TS_HOSTNAME=korail-bot"
            die "Nothing was started."
        fi
        return 0
    fi

    if [[ -z "$(clean_default "$(env_value MINI_APP_API_ENABLED)")" ]]; then
        warn "TS_AUTHKEY is set but MINI_APP_API_ENABLED is not - the sidecar will"
        warn "  proxy to a port nothing is listening on."
    fi

    set_env_key TS_SERVE_MODE "$mode"
    write_tailscale_serve_config "$mode"

    mkdir -p "$(tailscale_state_dir)"

    if [[ "$mode" == "funnel" ]]; then
        info "Mini App exposure: public (Funnel)"
    else
        info "Mini App exposure: tailnet only (Serve)"
        info "  Publish it with: $(basename "$0") --publish up"
    fi
}

# deploy_report_tailscale - say where the Mini App actually ended up
#
# Asked of the running node rather than assembled from TS_HOSTNAME, because
# the tailnet's domain is not in this checkout and a URL that is nearly right
# is worse than saying nothing.
deploy_report_tailscale() {
    tailscale_enabled || return 0

    local url deadline
    # The node has to register with the control plane before it has a name.
    deadline=$(( SECONDS + 45 ))
    until url="$(tailscale_url)" && [[ -n "$url" ]]; do
        (( SECONDS < deadline )) || break
        sleep 2
    done

    if [[ -z "${url:-}" ]]; then
        warn "The tailscale sidecar has not reported a name yet."
        warn "  Check it with: docker logs $(compose_container tailscale)"
        return 0
    fi

    echo
    ok "Mini App: ${url}"
    if [[ "$(tailscale_serve_mode)" == "funnel" ]]; then
        info "Reachable from the internet. Put this in ${ENV_FILE#"$ROOT_DIR"/}:"
        info "    MINI_APP_URL=${url}"
    else
        info "Reachable from your tailnet only."
    fi
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

if (( TEST_STACK )); then
    SETUP_COMMAND="scripts/setup.sh --test"
else
    SETUP_COMMAND="scripts/setup.sh secrets"
fi

STARTS_APP=1
if (( ${#SERVICES[@]} )); then
    STARTS_APP=0
    for service in "${SERVICES[@]}"; do
        [[ "$service" == "app" ]] && STARTS_APP=1
    done
fi

if (( STARTS_APP )); then
    TOKEN="$(clean_default "$(env_value BOTTOKEN)")"
    [[ -n "$TOKEN" ]] || die "BOTTOKEN is empty in ${ENV_FILE#"$ROOT_DIR"/}. Run '${SETUP_COMMAND}'."

    if (( TEST_STACK )) && [[ -f "$PRODUCTION_ENV_FILE" ]]; then
        PRODUCTION_TOKEN="$(sed -n 's/^BOTTOKEN=//p' "$PRODUCTION_ENV_FILE" | tail -n 1)"
        if [[ -n "$PRODUCTION_TOKEN" && "$TOKEN" == "$PRODUCTION_TOKEN" ]]; then
            die ".env.test must use a different BOTTOKEN from .env. Create a separate bot with BotFather."
        fi
    fi
fi

# docker-compose.yml refuses to start Redis without a password.
if [[ -z "$(env_value REDIS_PASSWORD)" ]]; then
    die "REDIS_PASSWORD is empty in ${ENV_FILE#"$ROOT_DIR"/}. Run '${SETUP_COMMAND}'."
fi

deploy_prepare_tailscale

if [[ "$PULL" -eq 1 ]]; then
    info "Pulling images"
    compose pull ${SERVICES[@]+"${SERVICES[@]}"}
fi

if [[ "$DETACH" -eq 1 ]]; then
    info "Starting services"
    compose up -d ${SERVICES[@]+"${SERVICES[@]}"}
    echo
    compose ps
    deploy_report_tailscale
    echo
    if (( TEST_STACK )); then
        info "Follow the logs with: scripts/deploy.sh --test logs"
    else
        info "Follow the logs with: scripts/deploy.sh logs"
    fi
else
    info "Starting services in the foreground (Ctrl-C to stop)"
    compose up ${SERVICES[@]+"${SERVICES[@]}"}
fi

}

deploy_down() {

require_cmd docker

PURGE_DATA=0
for arg in "$@"; do
    case "$arg" in
        # --volumes used to be the way to wipe the dataset, back when it lived
        # in a named volume. It is kept as a spelling of --purge-data so it
        # cannot quietly do nothing for someone who means to reset the bot.
        --purge-data|--volumes|-v) PURGE_DATA=1 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
require_env_file

DATA_DIR="$(redis_data_dir)"

info "Stopping services"
compose down
ok "Services stopped"

if [[ "$PURGE_DATA" -eq 0 ]]; then
    info "Redis data kept in ${DATA_DIR}"
    return 0
fi

warn "This deletes ${DATA_DIR}: all sessions, registered accounts and"
warn "reservation state go away."
read -r -p "Type 'yes' to continue: " confirmation
[[ "$confirmation" == "yes" ]] || die "Aborted."

# Through a container: Redis owns those files as its own uid, so removing them
# from the host would need root.
docker run --rm -v "${DATA_DIR}:/data" redis:7-alpine \
    sh -c 'rm -rf /data/appendonlydir /data/dump.rdb'
compose down --volumes >/dev/null 2>&1 || true
ok "Data removed from ${DATA_DIR}"

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
require_env_file

ARGS=(logs --tail "$TAIL")
[[ "$FOLLOW" -eq 1 ]] && ARGS+=(--follow)

compose "${ARGS[@]}" ${SERVICES[@]+"${SERVICES[@]}"}

}

deploy_push() {

case "${1:-}" in
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_cmd docker

if (( TEST_STACK )) && [[ -z "${1:-}" ]]; then
    require_env_file
    IMAGE="$(env_value IMAGE_NAME)"
else
    IMAGE="${1:-${IMAGE_NAME:-}}"
fi
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
