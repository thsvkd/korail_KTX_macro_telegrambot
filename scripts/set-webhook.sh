#!/usr/bin/env bash
#
# Register, inspect or remove the Telegram webhook.
#
# The webhook is registered together with TELEGRAM_WEBHOOK_SECRET from .env.
# Telegram then echoes that secret in the X-Telegram-Bot-Api-Secret-Token
# header of every update, which is what the app checks before trusting a
# request. Register it again whenever the secret is rotated.
#
# Usage:
#   scripts/set-webhook.sh https://your.domain/telebot   # register
#   scripts/set-webhook.sh --info                        # show current status
#   scripts/set-webhook.sh --delete                      # unregister

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

require_cmd curl

ACTION="set"
URL=""

case "${1:-}" in
    --info)   ACTION="info" ;;
    --delete) ACTION="delete" ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") die "Missing webhook URL. See 'scripts/set-webhook.sh --help'." ;;
    *)  URL="$1" ;;
esac

load_env
[[ -n "${BOTTOKEN:-}" ]] || die "BOTTOKEN is not set in .env"

API="https://api.telegram.org/bot${BOTTOKEN}"

case "$ACTION" in
    info)
        info "Fetching webhook info"
        # The response echoes the configured URL, so keep it off shared screens.
        curl -sS "${API}/getWebhookInfo"
        echo
        ;;
    delete)
        info "Deleting the webhook"
        curl -sS -X POST "${API}/deleteWebhook"
        echo
        ok "Webhook removed. The bot will stop receiving updates."
        ;;
    set)
        [[ "$URL" =~ ^https:// ]] || die "Telegram only accepts HTTPS webhook URLs."
        [[ -n "${TELEGRAM_WEBHOOK_SECRET:-}" ]] || \
            die "TELEGRAM_WEBHOOK_SECRET is not set. Run 'scripts/gen-secrets.sh'."

        info "Registering webhook: ${URL}"
        # allowed_updates is stated rather than left to the default: the
        # value is remembered between calls, so a webhook registered by an
        # older version of this script would keep filtering out the button
        # presses every inline keyboard in the bot depends on.
        curl -sS -X POST "${API}/setWebhook" \
            --data-urlencode "url=${URL}" \
            --data-urlencode "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
            --data-urlencode 'allowed_updates=["message","callback_query"]' \
            --data-urlencode "drop_pending_updates=true"
        echo
        ok "Webhook registered with a secret token."
        info "Verify with: scripts/set-webhook.sh --info"
        ;;
esac
