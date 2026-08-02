#!/usr/bin/env bash
#
# Make this machine able to run the project.
#
# The environment and nothing else: uv, the interpreter it downloads, .venv,
# and the dependencies pinned in uv.lock. It never writes .env, never
# generates a secret and never asks a question, so it is safe to run at any
# moment - after a pull, before a test, from inside another script - and
# running it twice leaves the same machine as running it once.
#
# That is the whole point of it being separate from setup.sh: setup decides
# things, and a decision is not something to repeat behind the user's back.
#
# Usage:
#   scripts/bootstrap.sh           # install or update .venv from uv.lock
#   scripts/bootstrap.sh --quiet   # the same, with output only when it fails
#   scripts/bootstrap.sh --check   # report what is missing, change nothing

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

QUIET=0
CHECK_ONLY=0

for arg in "$@"; do
    case "$arg" in
        -q|--quiet) QUIET=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"

# say - info() unless --quiet asked for silence
say() { (( QUIET )) || info "$*"; }

# ==================== Report only ====================

# Docker is not required to run the bot on the host - server.sh starts a Redis
# container when it needs one, and only then - so its absence is reported
# rather than treated as a failure. The Python side is a different matter:
# without uv there is no interpreter and nothing can run at all.
if (( CHECK_ONLY )); then
    MISSING=0

    if command -v uv >/dev/null 2>&1; then
        ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
    else
        err "uv is not installed"
        err "  curl -LsSf https://astral.sh/uv/install.sh | sh"
        MISSING=1
    fi

    if has_venv; then
        ok ".venv exists ($("${ROOT_DIR}/.venv/bin/python" -V 2>&1))"
    else
        err ".venv is missing - run 'scripts/bootstrap.sh'"
        MISSING=1
    fi

    if ! command -v docker >/dev/null 2>&1; then
        warn "docker is not installed - needed for Redis, the compose stack and the integration tests"
    elif ! docker info >/dev/null 2>&1; then
        warn "the docker daemon is not reachable - check that you are in the 'docker' group"
    elif has_compose; then
        ok "docker + compose $(compose_version)"
    else
        warn "docker is present but Docker Compose is not - 'scripts/deploy.sh' needs it"
    fi

    exit "$MISSING"
fi

# ==================== Install ====================

require_uv

# No interpreter search: uv reads requires-python from pyproject.toml and
# downloads that version when the host has not got it.
say "Syncing .venv with uv.lock"

SYNC=(uv sync --frozen)
(( QUIET )) && SYNC+=(--quiet)

if ! "${SYNC[@]}"; then
    err "Could not prepare the environment from uv.lock."
    die "Run 'uv lock' if it is out of step with pyproject.toml."
fi

(( QUIET )) && exit 0

ok "Dependencies installed into .venv ($(uv run --frozen python -V))"
