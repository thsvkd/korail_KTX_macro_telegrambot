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

# Tests must not depend on the developer's .env; uv does not load it, so
# there is nothing to switch off here. (A REDIS_PASSWORD from .env would
# fail against the throwaway container, which runs without auth.)

if [[ ${#PYTEST_ARGS[@]} -eq 0 ]]; then
    PYTEST_ARGS=(tests/ -v)
fi

# Only the integration and e2e suites need the throwaway Redis. conftest.py
# decides from the same paths; this check just reports it earlier.
if [[ "${PYTEST_ARGS[*]}" != tests/unit* ]] && ! docker info >/dev/null 2>&1; then
    warn "Docker does not look available."
    warn "tests/integration and tests/e2e need it for a temporary Redis."
    warn "Run 'scripts/test.sh tests/unit' for the suite that does not."
fi

# Values the app expects at import time. conftest.py sets the same defaults,
# these just keep a direct pytest invocation working too.
export BOTTOKEN="${BOTTOKEN:-test-bot-token}"
export TELEGRAM_WEBHOOK_SECRET="${TELEGRAM_WEBHOOK_SECRET:-test-webhook-secret}"
export SESSION_SECRET="${SESSION_SECRET:-test-session-secret}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-test-admin-password}"

require_uv
info "Running pytest ${PYTEST_ARGS[*]}"
exec uv run --frozen pytest "${PYTEST_ARGS[@]}"
