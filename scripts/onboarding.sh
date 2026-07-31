#!/usr/bin/env bash
#
# Interactive first-run setup: from a fresh clone to a bot that answers.
#
# Walks through environment checks, the bot token, secrets, how to run, the
# Korail account, access control, and finally a real round trip through
# Telegram so the user knows it works before they rely on it.
#
# Usage:
#   scripts/onboarding.sh
#   scripts/onboarding.sh --reset   # start over, ignoring the current .env

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

TOTAL_STEPS=8
RESET=0

for arg in "$@"; do
    case "$arg" in
        --reset) RESET=1 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

cd "$ROOT_DIR"

# ---------------------------------------------------------------- 표시 헬퍼

hr() { printf '%s\n' "────────────────────────────────────────────────────────"; }

step() {
    echo
    printf '%s\n' "${C_BLUE}━━━ [$1/${TOTAL_STEPS}] $2 ${C_RESET}"
    echo
}

say()  { printf '  %s\n' "$*"; }
note() { printf '  %s %s\n' "${C_YELLOW}!${C_RESET}" "$*"; }


# clean_default <value> - drop .env.example placeholders so that pressing
# Enter never stores a sample value as if it were real
# ask_choice <prompt> <default_index> <label...> - echoes the chosen index
ask_choice() {
    local prompt="$1" default="$2"; shift 2
    local labels=("$@") answer index
    printf '  %s\n' "$prompt" >&2
    for index in "${!labels[@]}"; do
        printf '    %d) %s\n' "$((index + 1))" "${labels[$index]}" >&2
    done
    while true; do
        printf '  선택 [%s]: ' "$default" >&2
        read -r answer
        answer="${answer:-$default}"
        if [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= ${#labels[@]} )); then
            printf '%s' "$answer"
            return 0
        fi
        printf '  %s\n' "1~${#labels[@]} 중에서 골라주세요." >&2
    done
}

# ------------------------------------------------------------------- 시작

clear 2>/dev/null || true
hr
echo "  🚄 코레일 KTX 예매 봇 — 설정 마법사"
hr
echo
say "매진된 열차를 계속 지켜보다가 취소표가 나오면 자동으로 잡아주는 봇입니다."
say "설정이 끝나면 이후 예매는 전부 텔레그램 대화로 진행합니다."
echo
say "중간에 언제든 Ctrl-C로 중단할 수 있고, 다시 실행하면 이어서 진행됩니다."
echo

if [[ -f "$ENV_FILE" && "$RESET" -eq 0 ]]; then
    note "이미 .env 설정이 있습니다. 값을 하나씩 확인하며 진행합니다."
    note "처음부터 새로 하시려면 Ctrl-C 후 'scripts/onboarding.sh --reset'을 실행하세요."
    echo
fi

if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$ENV_EXAMPLE" ]] || die ".env.example이 없습니다. 저장소가 온전한지 확인해주세요."
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok ".env 파일을 만들었습니다."
fi

# =================================================== [1] 환경 점검

step 1 "환경 점검"

HAS_DOCKER=0
DOCKER_NOTE=""

if command -v python3 >/dev/null 2>&1; then
    say "✔ python3 ($(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'))"
else
    die "python3가 필요합니다. 설치 후 다시 실행해주세요."
fi

if ! command -v docker >/dev/null 2>&1; then
    DOCKER_NOTE="docker가 설치되어 있지 않습니다"
elif ! docker info >/dev/null 2>&1; then
    DOCKER_NOTE="docker 데몬에 접근할 수 없습니다 (docker 그룹에 속해 있는지 확인하세요)"
elif ! has_compose; then
    # docker alone is not enough: the stack is defined as a compose project.
    DOCKER_NOTE="docker는 있지만 Docker Compose가 없습니다"
    say "  ${C_YELLOW}설치 (권장, sudo 불필요):${C_RESET}"
    say "    mkdir -p ~/.docker/cli-plugins"
    say "    curl -SL \"https://github.com/docker/compose/releases/latest/download/docker-compose-linux-\$(uname -m)\" \\"
    say "      -o ~/.docker/cli-plugins/docker-compose"
    say "    chmod +x ~/.docker/cli-plugins/docker-compose"
    say "  설치 후 이 스크립트를 다시 실행하면 Docker로 진행할 수 있습니다."
else
    HAS_DOCKER=1
    say "✔ docker + compose 사용 가능 (compose $(compose_version))"
    if command -v systemctl >/dev/null 2>&1; then
        if [[ "$(systemctl is-enabled docker 2>/dev/null)" == "enabled" ]]; then
            say "✔ docker 데몬이 부팅 시 자동 시작됩니다 (재부팅 후에도 봇이 살아납니다)"
        else
            note "docker 데몬이 부팅 시 자동 시작되지 않습니다."
            note "  재부팅 후에도 봇을 돌리려면: sudo systemctl enable docker"
        fi
    fi
fi

[[ -n "$DOCKER_NOTE" ]] && note "$DOCKER_NOTE"

# CI publishes linux/amd64 and linux/arm64, but any tag pushed before that
# change is amd64-only. Building locally works on either, so non-x86_64 hosts
# keep doing that rather than depending on which tag is currently up.
ARCH="$(uname -m)"
say "✔ 아키텍처: ${ARCH}"
if [[ "$ARCH" != "x86_64" && "$HAS_DOCKER" -eq 1 ]]; then
    note "이 기기에서는 이미지를 직접 빌드합니다."
    note "  (뒤에서 자동으로 처리합니다)"
fi

# =================================================== [2] 사용 형태

step 2 "사용 형태"

USAGE_CHOICE="$(ask_choice "이 봇을 누가 사용하나요?" 1 \
    "나 혼자 사용합니다 (권장)" \
    "여러 명이 함께 사용합니다")"

if [[ "$USAGE_CHOICE" == "1" ]]; then
    USAGE_MODE="solo"
    say "→ 혼자 사용. 코레일 로그인을 간편하게 설정해 드립니다."
else
    USAGE_MODE="multi"
    say "→ 여러 명 사용. 각자 텔레그램에서 코레일 정보를 입력하게 됩니다."
fi

echo
say "봇이 텔레그램 메시지를 받는 방법은 두 가지입니다."
say "  • 폴링  : 봇이 텔레그램에 물어봅니다. 공개 주소가 필요 없습니다."
say "  • 웹훅  : 텔레그램이 내 서버로 보냅니다. 공개 도메인 + HTTPS가 필요합니다."
echo

if ask_yn "인터넷에서 접속 가능한 도메인(HTTPS)을 가지고 계신가요?" n; then
    RECEIVE_MODE="webhook"
    say "→ 웹훅 모드로 설정합니다."
else
    RECEIVE_MODE="polling"
    say "→ 폴링 모드로 설정합니다. 공유기 뒤에서도 그대로 동작합니다."
fi

set_env_key RECEIVE_MODE "$RECEIVE_MODE"

# =================================================== [3] 봇 토큰

step 3 "텔레그램 봇 토큰"

CURRENT_TOKEN="$(env_value BOTTOKEN)"
BOT_USERNAME=""

# verify_token <token> - prints "@username" when Telegram accepts it
verify_token() {
    local token="$1" response
    response="$(curl -sS --max-time 15 "https://api.telegram.org/bot${token}/getMe" 2>/dev/null || true)"
    printf '%s' "$response" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not data.get("ok"):
    sys.exit(1)
print(data["result"].get("username", ""))
' 2>/dev/null
}

if [[ -n "$CURRENT_TOKEN" && ! "$CURRENT_TOKEN" =~ ^your_.*_here$ ]]; then
    say "기존 토큰이 있습니다. 확인 중..."
    if BOT_USERNAME="$(verify_token "$CURRENT_TOKEN")" && [[ -n "$BOT_USERNAME" ]]; then
        ok "@${BOT_USERNAME} 확인됨"
        if ! ask_yn "이 봇을 계속 사용하시겠어요?" y; then
            CURRENT_TOKEN=""
            BOT_USERNAME=""
        fi
    else
        note "기존 토큰이 유효하지 않습니다. 새로 입력해주세요."
        CURRENT_TOKEN=""
    fi
fi

if [[ -z "$BOT_USERNAME" ]]; then
    echo
    say "봇 토큰 발급 방법:"
    say "  1. 텔레그램에서 @BotFather 를 검색해 대화를 엽니다"
    say "  2. /newbot 을 입력합니다"
    say "  3. 봇 이름과 아이디(_bot 으로 끝나야 함)를 정합니다"
    say "  4. 받은 토큰을 아래에 붙여넣습니다"
    echo
    say "토큰은 '123456789:AAH...' 형태입니다."
    echo

    while true; do
        TOKEN="$(ask_secret "봇 토큰")"

        if [[ -z "$TOKEN" ]]; then
            note "토큰을 입력해주세요."
            continue
        fi

        if [[ ! "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            note "형식이 올바르지 않습니다. <숫자>:<문자열> 형태여야 합니다."
            continue
        fi

        say "텔레그램에 확인 중..."
        if BOT_USERNAME="$(verify_token "$TOKEN")" && [[ -n "$BOT_USERNAME" ]]; then
            set_env_key BOTTOKEN "$TOKEN"
            ok "@${BOT_USERNAME} 연결 확인!"
            break
        fi

        note "텔레그램이 이 토큰을 거부했습니다. 복사가 잘못되었는지 확인해주세요."
    done
fi

# =================================================== [4] 시크릿 생성

step 4 "보안 키 생성"

say "세션 암호화 키, Redis 비밀번호 등을 자동으로 만듭니다."
echo
"${SCRIPT_DIR}/gen-secrets.sh" || die "시크릿 생성에 실패했습니다."

# Auto-resume needs the Korail password to survive a restart, which is only
# possible while SESSION_SECRET can decrypt it.
echo
say "재부팅이나 재시작으로 검색이 끊겼을 때 자동으로 이어서 검색할 수 있습니다."
say "이 기능을 쓰면 검색이 진행되는 동안 코레일 비밀번호가 암호화되어 보관됩니다."
say "(검색이 끝나거나 취소되면 즉시 삭제됩니다)"
echo

if ask_yn "재시작 후 자동으로 검색을 이어갈까요?" y; then
    set_env_key RESUME_ON_RESTART "true"
    say "→ 자동 재개를 켭니다."
else
    set_env_key RESUME_ON_RESTART "false"
    say "→ 재시작 시 알림만 보내고 검색은 중단됩니다."
fi

# =================================================== [5] 실행 방식

step 5 "실행 방식"

RUN_MODE="local"

if [[ "$HAS_DOCKER" -eq 1 ]]; then
    RUN_CHOICE="$(ask_choice "어떻게 실행할까요?" 1 \
        "Docker (권장 - 재부팅 후 자동 복구, Redis 포함)" \
        "직접 실행 (uv - 개발용)")"
    [[ "$RUN_CHOICE" == "1" ]] && RUN_MODE="docker"
else
    note "docker를 쓸 수 없어 직접 실행으로 진행합니다."
fi

if [[ "$RUN_MODE" == "docker" ]]; then
    OVERRIDE_FILE="${ROOT_DIR}/docker-compose.override.yml"

    # docker-compose.yml pulls the published image, which lags this checkout.
    # A local override builds from the working tree instead. CI copies only
    # docker-compose.yml, so this stays local.
    {
        echo "# 온보딩이 생성한 로컬 개발용 오버라이드입니다."
        echo "# docker-compose.yml은 배포된 이미지를 받아 쓰지만, 여기서는"
        echo "# 이 저장소 소스로 직접 빌드합니다."
        echo "services:"
        echo "  app:"
        echo "    build: ."
        echo "    image: korailbot:local"
        # Compose appends sequences when merging, so dropping the inherited
        # port mapping needs the !reset tag (Compose v2.24+).
        if [[ "$RECEIVE_MODE" == "polling" ]] && compose_supports_reset; then
            echo "    # 폴링 모드에서는 외부에서 들어올 요청이 없습니다."
            echo "    ports: !reset []"
        fi
    } > "$OVERRIDE_FILE"

    ok "docker-compose.override.yml 생성 완료"
    if [[ "$RECEIVE_MODE" == "polling" ]]; then
        if compose_supports_reset; then
            say "  폴링 모드라 외부에 공개하는 포트가 없습니다."
        else
            note "폴링 모드에서는 8000 포트를 공개할 필요가 없지만,"
            note "  이 Compose 버전(≥2.24 필요)에서는 자동으로 제거할 수 없습니다."
            note "  원하시면 docker-compose.yml의 app.ports 항목을 지우세요."
        fi
    fi

    # Docker's default REDIS_HOST is the compose service name.
    set_env_key REDIS_HOST "redis"

    echo
    note "첫 빌드는 라즈베리파이에서 10분 이상 걸릴 수 있습니다."
    if ask_yn "지금 이미지를 빌드할까요?" y; then
        say "빌드 중... (로그가 길게 출력됩니다)"
        if compose build; then
            ok "이미지 빌드 완료"
        else
            note "빌드에 실패했습니다. 나중에 'docker compose build'로 다시 시도하세요."
        fi
    else
        say "→ 나중에 'docker compose build'로 빌드하세요."
    fi
else
    set_env_key REDIS_HOST "localhost"
    echo
    if ask_yn "파이썬 의존성을 지금 설치할까요? (scripts/setup.sh)" y; then
        "${SCRIPT_DIR}/setup.sh" || note "의존성 설치에 실패했습니다. 나중에 다시 시도하세요."
    fi
fi

# =================================================== [6] 코레일 계정

step 6 "코레일 계정"

if [[ "$USAGE_MODE" == "solo" ]]; then
    say "혼자 사용하실 때는 코레일 계정을 여기에 저장해두는 편이 낫습니다."
    say "매번 채팅에 비밀번호를 입력하지 않아도 되고, 무엇보다"
    say "${C_YELLOW}비밀번호가 텔레그램 대화 기록에 남지 않습니다.${C_RESET}"
    echo
    say "저장한 계정은 ${C_YELLOW}개발자 방에서만${C_RESET} 쓰입니다. 아래에서 만들어 드리는"
    say "문구를 텔레그램으로 보내면 그 채팅방이 개발자 방이 됩니다."
    echo

    if ask_yn "코레일 계정을 저장할까요?" y; then
        KORAIL_ID="$(ask "코레일 회원번호 (예: 010-1234-5678)" "$(clean_default "$(env_value USERID)")")"
        KORAIL_PW="$(ask_secret "코레일 비밀번호")"

        if [[ -n "$KORAIL_ID" && -n "$KORAIL_PW" ]]; then
            set_env_key USERID "$KORAIL_ID"
            set_env_key USERPW "$KORAIL_PW"

            # 직접 정하게 두지 않는다. 이 문구를 아는 사람은 누구나 개발자 방을
            # 만들 수 있는데, 사람이 고른 문구는 짧고 추측하기 쉽다.
            MAGIC="$(env_value ADMIN_MAGIC_STRING)"
            [[ -n "$MAGIC" ]] || MAGIC="$(gen_secret 24)"
            set_env_key ADMIN_MAGIC_STRING "$MAGIC"

            echo
            ok "설정 완료. 봇을 띄운 뒤 아래 문구를 텔레그램으로 보내세요."
            say "${C_YELLOW}${MAGIC}${C_RESET}"
            say "보낸 채팅방이 개발자 방이 되고, 그 방에서는 로그인 없이 바로 예약합니다."
            say "해제는 /devoff, 공유는 금물입니다."
        else
            note "입력이 비어 있어 건너뜁니다."
        fi
    else
        say "→ 매번 채팅에서 코레일 정보를 입력하게 됩니다."
    fi
else
    say "여러 명이 사용하므로 각자 텔레그램에서 코레일 정보를 입력합니다."
    say "서버에는 코레일 계정을 저장하지 않습니다."
    # A magic string in a shared bot would let any user log in as the owner.
    set_env_key ADMIN_MAGIC_STRING ""
fi

# =================================================== [7] 접근 제어

step 7 "사용자 제한"

say "봇을 아무나 쓰지 못하게 전화번호로 제한할 수 있습니다."
say "비워두면 봇 주소를 아는 누구나 사용할 수 있습니다."
echo

CURRENT_ALLOW="$(env_value ALLOW_LIST)"
[[ "$CURRENT_ALLOW" == *"010-1234-5678"* ]] && CURRENT_ALLOW=""
DEFAULT_ALLOW="${CURRENT_ALLOW:-${KORAIL_ID:-}}"

ALLOW_INPUT="$(ask "허용할 전화번호 (쉼표로 구분, 비우면 제한 없음)" "$DEFAULT_ALLOW")"
set_env_key ALLOW_LIST "$ALLOW_INPUT"

if [[ -z "$ALLOW_INPUT" ]]; then
    note "제한 없음으로 설정했습니다. 봇 주소가 알려지면 누구나 사용할 수 있습니다."
else
    ok "허용 목록: ${ALLOW_INPUT}"
fi

# =================================================== [8] 연결 확인

step 8 "연결 확인"

TOKEN="$(env_value BOTTOKEN)"
CHAT_ID=""

# getUpdates has exactly one consumer per token, so a running bot would fight
# this check for the same updates.
if pgrep -f "python[0-9.]* .*src/app\.py" >/dev/null 2>&1; then
    note "봇이 이미 실행 중이라 연결 확인을 건너뜁니다."
    note "  확인하시려면 봇을 멈춘 뒤 이 단계를 다시 실행하세요."
elif ask_yn "지금 실제로 메시지를 주고받아 확인해볼까요?" y; then
    echo
    say "텔레그램에서 ${C_GREEN}@${BOT_USERNAME}${C_RESET} 을 열고"
    say "아무 메시지나 하나 보내주세요. (예: 안녕)"
    echo
    say "기다리는 중... (최대 2분, Ctrl-C로 건너뛰기)"

    DEADLINE=$((SECONDS + 120))
    while (( SECONDS < DEADLINE )); do
        RESPONSE="$(curl -sS --max-time 35 \
            "https://api.telegram.org/bot${TOKEN}/getUpdates?timeout=25&offset=-1" \
            2>/dev/null || true)"

        RESULT="$(printf '%s' "$RESPONSE" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if not data.get("ok"):
    if data.get("error_code") == 409:
        print("CONFLICT")
    sys.exit(0)

for update in reversed(data.get("result", [])):
    message = update.get("message")
    if message and "chat" in message:
        chat = message["chat"]
        name = chat.get("first_name") or chat.get("title") or "사용자"
        print(f"{chat[\"id\"]}\t{name}")
        break
' 2>/dev/null || true)"

        if [[ "$RESULT" == "CONFLICT" ]]; then
            note "다른 곳에서 이 봇이 이미 실행 중입니다. 그쪽을 멈춘 뒤 다시 시도하세요."
            break
        fi

        if [[ -n "$RESULT" ]]; then
            CHAT_ID="${RESULT%%$'\t'*}"
            SENDER="${RESULT#*$'\t'}"
            ok "${SENDER}님의 메시지를 받았습니다!"

            curl -sS --max-time 15 -X POST \
                "https://api.telegram.org/bot${TOKEN}/sendMessage" \
                --data-urlencode "chat_id=${CHAT_ID}" \
                --data-urlencode "text=✅ 설정이 확인되었습니다! 이제 /start 로 예매를 시작할 수 있습니다." \
                >/dev/null 2>&1 || true

            ok "답장을 보냈습니다. 텔레그램을 확인해보세요."
            break
        fi
    done

    [[ -z "$CHAT_ID" ]] && note "메시지를 받지 못했습니다. 나중에 다시 확인해도 됩니다."
fi

# =================================================== 마무리

echo
hr
echo "  설정 점검"
hr
echo
"${SCRIPT_DIR}/security-check.sh" || true

echo
hr
echo "  ✅ 설정 완료"
hr
echo
say "실행 방법:"
if [[ "$RUN_MODE" == "docker" ]]; then
    say "  docker compose up -d          # 시작 (재부팅 후 자동 복구)"
    say "  scripts/docker-logs.sh        # 로그 보기"
    say "  docker compose down           # 중지"
else
    say "  scripts/dev-redis.sh          # Redis 시작"
    say "  scripts/run.sh                # 봇 시작"
fi

if [[ "$RECEIVE_MODE" == "webhook" ]]; then
    echo
    note "웹훅 모드입니다. 봇을 실행한 뒤 아래 명령으로 주소를 등록하세요:"
    say "  scripts/set-webhook.sh https://your.domain/telebot"
fi

echo
say "예매하는 방법:"
say "  1. 텔레그램에서 @${BOT_USERNAME} 열기"
say "  2. /start 입력"
MAGIC_VALUE="$(env_value ADMIN_MAGIC_STRING)"
if [[ -n "$MAGIC_VALUE" ]]; then
    say "  3. '예' → '${MAGIC_VALUE}' 입력하면 로그인 완료"
else
    say "  3. '예' → 코레일 회원번호와 비밀번호 입력"
fi
say "  4. 날짜(20260815) → 출발역(서울) → 도착역(부산) 순으로 답하기"
say "  5. 시간대·인원까지 답하면 검색이 시작됩니다"
echo
say "취소표가 나오면 자동으로 예약하고 알려드립니다."
note "예약 후 ${C_YELLOW}10분 안에 코레일에서 직접 결제${C_RESET}해야 취소되지 않습니다."
echo
say "  /status  진행 상황 확인"
say "  /cancel  검색 중단"
echo
