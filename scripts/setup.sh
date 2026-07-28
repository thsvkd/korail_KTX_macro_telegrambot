#!/usr/bin/env bash
#
# Prepare a development environment: dependencies, .env, secrets.
#
# Usage:
#   scripts/setup.sh            # install dev dependencies and create .env
#   scripts/setup.sh --no-deps  # only create .env and secrets

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

INSTALL_DEPS=1
for arg in "$@"; do
    case "$arg" in
        --no-deps) INSTALL_DEPS=0 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR"

# ---------------------------------------------------------------- .env file
if [[ -f "$ENV_FILE" ]]; then
    info ".env already exists - keeping it"
else
    [[ -f "$ENV_EXAMPLE" ]] || die ".env.example is missing"
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "Created .env from .env.example"
fi

# ------------------------------------------------------------------ secrets
info "Generating missing secrets"
"${SCRIPT_DIR}/gen-secrets.sh"

# ------------------------------------------------------------- dependencies
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
    require_cmd pipenv "Install it with 'pip install --user pipenv' or 'brew install pipenv'."
    info "Installing Python dependencies (this can take a while)"
    pipenv install --dev
    ok "Dependencies installed"
else
    info "Skipping dependency installation (--no-deps)"
fi

# ------------------------------------------------------------------ summary
echo
ok "Setup complete."
echo
echo "Still to do by hand:"
echo "  1. Put your bot token in .env         -> BOTTOKEN="
echo "  2. Register the webhook                -> scripts/set-webhook.sh https://your.domain/telebot"
echo "  3. Start the app                       -> scripts/run.sh"
echo
echo "Optional:"
echo "  ALLOW_LIST         restrict who may use the bot"
echo "  USERID / USERPW    Korail account for the magic-string shortcut"
echo "  ADMIN_MAGIC_STRING trigger for that shortcut (empty = disabled)"
