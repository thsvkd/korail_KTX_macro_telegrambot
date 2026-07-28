#!/usr/bin/env bash
#
# Generate the secrets the bot needs and, unless --print is given, fill the
# empty ones in .env.
#
# Usage:
#   scripts/gen-secrets.sh              # fill empty secrets in .env
#   scripts/gen-secrets.sh --print      # print fresh values, touch nothing
#   scripts/gen-secrets.sh --force      # regenerate even if already set
#
# Note: rotating SESSION_SECRET makes stored sessions unreadable, so users
# have to enter their Korail credentials again. Rotating
# TELEGRAM_WEBHOOK_SECRET requires re-running scripts/set-webhook.sh.

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

MANAGED_KEYS=(TELEGRAM_WEBHOOK_SECRET SESSION_SECRET ADMIN_PASSWORD REDIS_PASSWORD)

MODE="fill"
for arg in "$@"; do
    case "$arg" in
        --print) MODE="print" ;;
        --force) MODE="force" ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

if [[ "$MODE" == "print" ]]; then
    for key in "${MANAGED_KEYS[@]}"; do
        printf '%s=%s\n' "$key" "$(gen_secret)"
    done
    exit 0
fi

require_env_file

# set_key <KEY> <VALUE> - replace the KEY= line in .env, appending if absent
set_key() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$ENV_FILE"; then
        # Use a python pass so that values containing / & \ survive intact.
        KEY="$key" VALUE="$value" python3 - "$ENV_FILE" > "$tmp" <<'PY'
import os, sys

key = os.environ["KEY"]
value = os.environ["VALUE"]
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        if line.startswith(f"{key}="):
            print(f"{key}={value}")
        else:
            print(line, end="")
PY
        mv "$tmp" "$ENV_FILE"
    else
        rm -f "$tmp"
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

changed=0
for key in "${MANAGED_KEYS[@]}"; do
    current="$(env_value "$key")"
    if [[ -n "$current" && "$MODE" != "force" ]]; then
        info "$key is already set - leaving it alone (use --force to rotate)"
        continue
    fi
    set_key "$key" "$(gen_secret)"
    ok "Generated $key"
    changed=1
done

chmod 600 "$ENV_FILE"

if [[ "$changed" -eq 1 ]]; then
    echo
    warn "Restart the app so the new values take effect."
    warn "If TELEGRAM_WEBHOOK_SECRET changed, re-run: scripts/set-webhook.sh <url>"
fi

ok "Secrets written to .env (mode 600)"
