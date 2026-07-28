#!/usr/bin/env bash
#
# Run the test suite.
#
# tests/conftest.py starts a throwaway Redis via testcontainers, so Docker
# has to be running. It is torn down when the run finishes.
#
# Usage:
#   scripts/test.sh                     # run everything
#   scripts/test.sh tests/unit          # run a subset
#   scripts/test.sh -k crypto           # pass any pytest flags through

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

PYTEST_ARGS=()
for arg in "$@"; do
    case "$arg" in
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) PYTEST_ARGS+=("$arg") ;;
    esac
done

cd "$ROOT_DIR"

# Tests must not depend on the developer's .env - pipenv would otherwise
# inject it (a REDIS_PASSWORD there fails against the throwaway container).
export PIPENV_DONT_LOAD_ENV=1

if ! docker info >/dev/null 2>&1; then
    warn "Docker does not look available."
    warn "The suite needs it to start a temporary Redis (testcontainers)."
fi

# Values the app expects at import time. conftest.py sets the same defaults,
# these just keep a direct pytest invocation working too.
export BOTTOKEN="${BOTTOKEN:-test-bot-token}"
export TELEGRAM_WEBHOOK_SECRET="${TELEGRAM_WEBHOOK_SECRET:-test-webhook-secret}"
export SESSION_SECRET="${SESSION_SECRET:-test-session-secret}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-test-admin-password}"
export PYTHONPATH="${ROOT_DIR}/src:${ROOT_DIR}:${PYTHONPATH:-}"

if [[ ${#PYTEST_ARGS[@]} -eq 0 ]]; then
    PYTEST_ARGS=(tests/ -v)
fi

info "Running pytest ${PYTEST_ARGS[*]}"
if has_venv; then
    pipenv run pytest "${PYTEST_ARGS[@]}"
elif python3 -c 'import pytest' >/dev/null 2>&1; then
    warn "No pipenv virtualenv found - falling back to the system pytest"
    python3 -m pytest "${PYTEST_ARGS[@]}"
else
    err "pytest is not available: there is no project virtualenv and the"
    err "system Python does not have pytest installed."
    die "Run 'scripts/setup.sh' to create the environment first."
fi
