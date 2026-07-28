#!/usr/bin/env bash
#
# Stop the stack.
#
# Usage:
#   scripts/docker-down.sh                # stop and remove containers
#   scripts/docker-down.sh --volumes      # also delete the Redis volume
#
# --volumes destroys every stored session, reservation and payment state.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

require_cmd docker

REMOVE_VOLUMES=0
for arg in "$@"; do
    case "$arg" in
        --volumes|-v) REMOVE_VOLUMES=1 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR"

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
