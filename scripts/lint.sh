#!/usr/bin/env bash
#
# Check the source without running it: formatting, lint rules, and - when the
# tools are there - types and the shell scripts themselves.
#
# Every check runs even after one of them fails, so a single pass reports
# everything there is to fix rather than the first thing.
#
# ruff is the gate: CI fails on it, so --fix is offered here to close that loop
# in one command. mypy and shellcheck report but do not fail the run; mypy
# still has a backlog of pre-existing errors, and shellcheck is not installed
# everywhere.
#
# Usage:
#   scripts/lint.sh          # check formatting and lint rules
#   scripts/lint.sh --fix    # reformat and autofix what ruff can
#   scripts/lint.sh --all    # also run mypy and shellcheck (report only)

# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

FIX=0
ALL=0

for arg in "$@"; do
    case "$arg" in
        --fix) FIX=1 ;;
        --all) ALL=1 ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR" || die "Cannot enter repository root: $ROOT_DIR"
require_uv

FAILURES=0

# run <label> <command...> - run a check, remember a failure, keep going
run() {
    local label="$1"; shift
    info "$label"
    if "$@"; then
        ok "$label"
        return 0
    fi
    err "$label failed"
    FAILURES=$((FAILURES + 1))
    return 0
}

if (( FIX )); then
    run "ruff format" uv run --frozen ruff format .
    run "ruff check --fix" uv run --frozen ruff check --fix .
else
    run "ruff format --check" uv run --frozen ruff format --check .
    run "ruff check" uv run --frozen ruff check .
fi

if (( ALL )); then
    echo
    # Report only: `mypy src` still has errors that predate this gate, so
    # failing on them would mean the command never passes and nobody runs it.
    info "mypy (report only)"
    uv run --frozen mypy || warn "mypy reported errors - not counted as a failure"

    echo
    if command -v shellcheck >/dev/null 2>&1; then
        info "shellcheck (report only)"
        shellcheck scripts/*.sh || warn "shellcheck reported findings - not counted as a failure"
    else
        info "shellcheck is not installed - skipping the shell scripts"
    fi
fi

echo
if (( FAILURES )); then
    die "${FAILURES} check(s) failed."
fi
ok "All checks passed."
