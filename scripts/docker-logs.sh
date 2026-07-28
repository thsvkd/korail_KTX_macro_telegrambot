#!/usr/bin/env bash
#
# Follow the container logs.
#
# Usage:
#   scripts/docker-logs.sh              # follow every service
#   scripts/docker-logs.sh app          # follow one service
#   scripts/docker-logs.sh --tail 200   # start further back
#   scripts/docker-logs.sh --no-follow  # print and exit

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

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

cd "$ROOT_DIR"

ARGS=(logs --tail "$TAIL")
[[ "$FOLLOW" -eq 1 ]] && ARGS+=(--follow)

compose "${ARGS[@]}" ${SERVICES[@]+"${SERVICES[@]}"}
