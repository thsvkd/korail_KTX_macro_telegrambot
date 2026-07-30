#!/usr/bin/env bash
#
# Build and publish the application image to a registry.
#
# Usage:
#   scripts/docker-push.sh myname/bot:v4      # push a specific tag
#   IMAGE_NAME=myname/bot:latest scripts/docker-push.sh
#
# There is no default tag: a registry namespace belongs to whoever runs this,
# and guessing one would either fail or push to someone else's. Set IMAGE_NAME
# or pass the tag.
#
# Publishing is what the deployed servers pull, so this asks for confirmation
# first. Log in beforehand with 'docker login'.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

case "${1:-}" in
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

require_cmd docker

IMAGE="${1:-${IMAGE_NAME:-}}"
[[ -n "$IMAGE" ]] || die "No image tag. Pass one, or set IMAGE_NAME (e.g. you/korailbot:latest)."

# A bare name with no registry namespace only exists on this machine; pushing
# it would either fail or, worse, land somewhere unintended.
[[ "$IMAGE" == */* ]] || die "'${IMAGE}' has no registry namespace - use something like you/korailbot:latest."

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
