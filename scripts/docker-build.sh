#!/usr/bin/env bash
#
# Build the application image from the local source.
#
# Usage:
#   scripts/docker-build.sh                 # build geunsam2/korailbot:dev
#   scripts/docker-build.sh myname/bot:v4   # build under a different tag
#
# Note: docker-compose.yml runs the published geunsam2/korailbot:latest image.
# To run what you just built, tag it accordingly or override the image in a
# compose override file.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_cmd docker

IMAGE="${1:-geunsam2/korailbot:dev}"

cd "$ROOT_DIR"

info "Building ${IMAGE}"
docker build -t "$IMAGE" .
ok "Built ${IMAGE}"

docker image inspect "$IMAGE" --format '  size: {{.Size}} bytes'
