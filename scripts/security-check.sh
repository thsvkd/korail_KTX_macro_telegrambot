#!/usr/bin/env bash
#
# Check the local configuration for the mistakes that actually hurt:
# missing secrets, a debug server, an exposed Redis, committed credentials.
#
# Usage:
#   scripts/security-check.sh
#
# Exits non-zero when at least one FAIL is reported.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

cd "$ROOT_DIR"

FAILURES=0
WARNINGS=0

pass_check() { printf '  %s %s\n' "${C_GREEN}PASS${C_RESET}" "$1"; }
fail_check() { printf '  %s %s\n' "${C_RED}FAIL${C_RESET}" "$1"; FAILURES=$((FAILURES + 1)); }
warn_check() { printf '  %s %s\n' "${C_YELLOW}WARN${C_RESET}" "$1"; WARNINGS=$((WARNINGS + 1)); }

# is_placeholder <value> - true for the sample values shipped in .env.example
is_placeholder() {
    [[ "$1" =~ ^your_.*_here$ ]]
}

echo "Configuration"

if [[ -f "$ENV_FILE" ]]; then
    pass_check ".env exists"

    perms="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%A' "$ENV_FILE" 2>/dev/null || echo '???')"
    if [[ "$perms" == "600" || "$perms" == "400" ]]; then
        pass_check ".env permissions are ${perms}"
    else
        warn_check ".env permissions are ${perms} - tighten with 'chmod 600 .env'"
    fi

    for key in BOTTOKEN TELEGRAM_WEBHOOK_SECRET; do
        value="$(env_value "$key")"
        if is_placeholder "$value"; then
            fail_check "${key} still holds the .env.example placeholder"
        elif [[ -n "$value" ]]; then
            pass_check "${key} is set"
        else
            fail_check "${key} is empty - the app will refuse to start"
        fi
    done

    for key in SESSION_SECRET REDIS_PASSWORD; do
        if [[ -n "$(env_value "$key")" ]]; then
            pass_check "${key} is set"
        else
            fail_check "${key} is empty - run 'scripts/gen-secrets.sh'"
        fi
    done

    if [[ -n "$(env_value ADMIN_PASSWORD)" ]]; then
        pass_check "ADMIN_PASSWORD is set"
        if [[ -n "$(env_value USERPW)" && "$(env_value ADMIN_PASSWORD)" == "$(env_value USERPW)" ]]; then
            fail_check "ADMIN_PASSWORD equals USERPW - guessing it would expose the Korail account"
        else
            pass_check "ADMIN_PASSWORD differs from USERPW"
        fi
    else
        warn_check "ADMIN_PASSWORD is empty - admin commands are disabled"
    fi

    debug_value="$(env_value FLASK_DEBUG)"
    if [[ -z "$debug_value" || "$debug_value" =~ ^([Ff]alse|0|[Nn]o|[Oo]ff)$ ]]; then
        pass_check "FLASK_DEBUG is off"
    else
        fail_check "FLASK_DEBUG=${debug_value} - the Werkzeug debugger allows remote code execution"
    fi
else
    fail_check ".env is missing - run 'scripts/setup.sh'"
fi

echo
echo "Repository"

if git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    if git -C "$ROOT_DIR" ls-files --error-unmatch .env >/dev/null 2>&1; then
        fail_check ".env is tracked by git - remove it from the index immediately"
    else
        pass_check ".env is not tracked by git"
    fi

    if git -C "$ROOT_DIR" check-ignore -q .env 2>/dev/null; then
        pass_check ".env is covered by .gitignore"
    else
        warn_check ".env is not covered by .gitignore"
    fi
else
    warn_check "Not a git repository - skipping repository checks"
fi

echo
echo "Deployment"

if grep -qE '^\s*-\s*"6379:6379"' "${ROOT_DIR}/docker-compose.yml"; then
    fail_check "docker-compose publishes Redis on the host (port 6379)"
else
    pass_check "Redis is not published to the host"
fi

if grep -q 'requirepass' "${ROOT_DIR}/docker-compose.yml"; then
    pass_check "Redis requires a password"
else
    fail_check "Redis starts without authentication"
fi

if grep -qE '^\s*uses:.*@(master|main)\s*$' "${ROOT_DIR}/.github/workflows/cicd.yml" 2>/dev/null; then
    warn_check "A GitHub Action is pinned to @master - pin it to a release tag"
else
    pass_check "GitHub Actions are pinned to releases"
fi

echo
if [[ "$FAILURES" -gt 0 ]]; then
    err "${FAILURES} failed check(s), ${WARNINGS} warning(s)"
    exit 1
fi

if [[ "$WARNINGS" -gt 0 ]]; then
    warn "All critical checks passed, ${WARNINGS} warning(s)"
    exit 0
fi

ok "All checks passed"
