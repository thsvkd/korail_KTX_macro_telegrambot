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

case "${1:-}" in
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

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

# check_required <KEY> - report a secret the app refuses to start without
check_required() {
    local key="$1"
    local value
    value="$(env_value "$key")"

    if is_placeholder "$value"; then
        fail_check "${key} still holds the .env.example placeholder"
    elif [[ -n "$value" ]]; then
        pass_check "${key} is set"
    else
        fail_check "${key} is empty - the app will refuse to start"
    fi
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

    check_required BOTTOKEN

    # The webhook secret is what keeps forged updates out of /telebot. In
    # polling mode there is no such endpoint to protect, so demanding one
    # would only train people to ignore this output.
    receive_mode="$(env_value RECEIVE_MODE)"
    receive_mode="${receive_mode:-polling}"
    case "$receive_mode" in
        webhook)
            check_required TELEGRAM_WEBHOOK_SECRET
            ;;
        polling)
            pass_check "RECEIVE_MODE=polling - no webhook endpoint is exposed"
            ;;
        *)
            fail_check "RECEIVE_MODE=${receive_mode} is neither 'polling' nor 'webhook' - the app will refuse to start"
            ;;
    esac

    # Telegram tokens look like <bot_id>:<35 chars>. Catching a mangled one
    # here beats discovering it when the first message fails to send.
    bottoken="$(env_value BOTTOKEN)"
    if [[ -n "$bottoken" ]] && ! is_placeholder "$bottoken"; then
        if [[ "$bottoken" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            pass_check "BOTTOKEN has the expected <bot_id>:<token> shape"
        else
            fail_check "BOTTOKEN is malformed - expected <digits>:<token>"
        fi
    fi

    for key in SESSION_SECRET REDIS_PASSWORD; do
        if [[ -n "$(env_value "$key")" ]]; then
            pass_check "${key} is set"
        else
            fail_check "${key} is empty - run 'scripts/gen-secrets.sh'"
        fi
    done

    # Resuming a search means reading back a password stored before the
    # restart, which an ephemeral key cannot do - the feature would fail
    # silently exactly when it is needed.
    resume_on_restart="$(env_value RESUME_ON_RESTART)"
    resume_on_restart="${resume_on_restart:-true}"
    if [[ "$resume_on_restart" =~ ^([Tt]rue|1|[Yy]es|[Oo]n)$ ]]; then
        if [[ -n "$(env_value SESSION_SECRET)" ]]; then
            pass_check "RESUME_ON_RESTART is on and SESSION_SECRET can decrypt it"
        else
            fail_check "RESUME_ON_RESTART is on but SESSION_SECRET is empty - interrupted searches can never be resumed"
        fi
    else
        pass_check "RESUME_ON_RESTART is off - no credentials are kept between restarts"
    fi

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

    # The sample list locks the owner out of their own bot with a confusing
    # "권한이 없습니다", which is not obviously a config problem.
    allow_list="$(env_value ALLOW_LIST)"
    if [[ -z "$allow_list" ]]; then
        warn_check "ALLOW_LIST is empty - anyone who finds the bot can use it"
    elif [[ "$allow_list" == *"010-1234-5678"* || "$allow_list" == *"010-9876-5432"* ]]; then
        fail_check "ALLOW_LIST still holds the .env.example sample numbers - real users will be refused"
    else
        pass_check "ALLOW_LIST is configured"
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
