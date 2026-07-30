#!/usr/bin/env bash
#
# Build the application image from the local source.
#
# Usage:
#   scripts/docker-build.sh                 # build korailbot:local
#   scripts/docker-build.sh myname/bot:v4   # build under a different tag
#
# korailbot:local is what docker-compose.yml runs by default, so a plain
# build here is immediately what `scripts/docker-up.sh` starts. Publishing
# under your own namespace is the override, not the default.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_cmd docker

IMAGE="${1:-${IMAGE_NAME:-korailbot:local}}"

cd "$ROOT_DIR"

info "Building ${IMAGE}"
docker build -t "$IMAGE" .
ok "Built ${IMAGE}"

docker image inspect "$IMAGE" --format '  size: {{.Size}} bytes'
