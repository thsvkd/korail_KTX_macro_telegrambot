#!/usr/bin/env bash
#
# Build and publish the application image to a registry.
#
# Usage:
#   scripts/docker-push.sh                    # push geunsam2/korailbot:latest
#   scripts/docker-push.sh myname/bot:v4      # push a specific tag
#
# Publishing is what the deployed servers pull, so this asks for confirmation
# first. Log in beforehand with 'docker login'.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

require_cmd docker

IMAGE="${1:-geunsam2/korailbot:latest}"

cd "$ROOT_DIR"

warn "About to publish ${IMAGE}."
warn "Anything pulling this tag will run the new image."
read -r -p "Type 'yes' to continue: " confirmation
[[ "$confirmation" == "yes" ]] || die "Aborted."

info "Building ${IMAGE}"
docker build -t "$IMAGE" .

info "Pushing ${IMAGE}"
docker push "$IMAGE"

ok "Published ${IMAGE}"
