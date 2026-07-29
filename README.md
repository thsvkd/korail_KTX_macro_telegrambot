# 코레일 KTX 예매 텔레그램 챗봇

매진된 KTX 열차를 자동으로 모니터링하여 좌석이 나오면 예약해주는 텔레그램 봇입니다.

## 빠른 시작

```bash
# uv 설치 (한 번만) - Python 인터프리터까지 알아서 받아옵니다
curl -LsSf https://astral.sh/uv/install.sh | sh

# 프로젝트 클론
git clone https://github.com/GeunSam2/korail_KTX_macro_telegrambot.git
cd korail_KTX_macro_telegrambot

# .env 생성 + 시크릿 발급 + 의존성 설치 (처음 한 번만)
./scripts/setup.sh

# .env 에 BOTTOKEN 입력

# 실행 (기본값인 폴링 모드는 공인 IP·HTTPS 없이 바로 동작합니다)
./scripts/run.sh

# 계속 띄워둘 거라면 백그라운드로. 상태는 status.sh 로 봅니다.
./scripts/run.sh --daemon
./scripts/status.sh
```

봇은 한 번에 하나만 돌아야 합니다 — 같은 토큰을 둘이 물면 텔레그램이 한쪽에
409 를 돌려주고 업데이트가 갈라집니다. 그래서 `run.sh` 는 기동 전에 이미
돌고 있는 봇을 먼저 정지시킵니다.

스크립트 전체 목록은 [scripts/README.md](scripts/README.md)를 참고하세요.

## 사용법

텔레그램에서 `/start` 를 누르면 예약 정보를 하나씩 물어봅니다. 선택지가
정해진 항목은 **버튼**으로 나오므로 타이핑할 필요가 없습니다.

| 단계 | 입력 방식 |
| --- | --- |
| 진행 확인 | 버튼 (예 / 아니오) |
| 휴대전화번호·비밀번호 | 직접 입력 |
| 출발 희망일 | 버튼 (오늘부터 9일) + 직접 입력 |
| 출발역·도착역 | 버튼 (주요 18개 역) + 직접 입력 |
| 검색 시작·종료 시각 | 버튼 (시 단위, 종료는 "제한 없음" 포함) + 직접 입력 |
| 열차 종류·좌석 종류·인원수·좌석 배치 | 버튼 |
| **감시할 열차** | 버튼 (해당 시간대 열차 목록, 다중 선택) + 열차번호 입력 |
| 최종 확인 | 버튼 (지금 시작 / 시작 시각 예약 / 취소) |

### 감시할 열차 고르기

마지막에서 두 번째 단계에서 **그 시간대에 운행하는 열차 목록**이 뜹니다.
매진된 열차도 함께 나옵니다 — 여석이 있는 열차는 감시할 이유가 없으니,
고를 만한 열차는 곧 매진된 열차입니다.

```
⬜ 06:03→08:49 KTX-산천   매진
☑️ 06:33→09:22 KTX        매진
⬜ 06:57→09:39 KTX        여석
     [ ▶️ 선택한 1개 열차로 시작 ]
     [ 🚄 시간대 전체 감시 (성공률 ↑) ]
```

- 여러 개 고를 수 있습니다. 고른 열차에만 취소표가 나오면 잡습니다.
- 다시 누르면 선택이 해제됩니다. `101 105` 처럼 열차번호를 직접 입력해도
  됩니다.
- **아무것도 고르지 않으면 시간대 전체를 감시합니다.** 이것이 원래 동작이고
  성공률도 훨씬 높습니다. 특정 열차를 고집할 이유(환승, 약속 시간)가 있을
  때만 좁히세요.
- 코레일 목록을 못 불러와도 흐름이 멈추지 않습니다. 전체 감시로 넘어갑니다.

- 모든 버튼 화면에 **취소** 버튼이 있어 `/cancel` 을 치지 않아도 빠져나올 수
  있습니다. 명령어는 텔레그램 메뉴 버튼에서도 고를 수 있습니다.
- 버튼 대신 예전처럼 **직접 입력해도 그대로 동작합니다.** 버튼이 보내는
  값은 타이핑했을 때와 똑같은 값이라, 두 방식이 같은 검증을 거칩니다.
- 지나간 단계의 버튼을 눌러도 무시됩니다. 채팅 기록에 남은 버튼은 계속
  눌리는데, 값이 대부분 한 자리 숫자라서 지금 단계가 그대로 받아버리면
  "특실만"을 누른 것이 "4명"으로 기록되기 때문입니다.

### 검색 시작 시각 예약

마지막 확인 화면에서 **지금 시작하는 대신 시각을 정할 수 있습니다.**
명절 예매가 열리는 시각처럼 표가 풀리는 때를 노릴 때 씁니다.

```
        [ 1시간 뒤 ] [ 3시간 뒤 ] [ 6시간 뒤 ]
     [ 오늘 22:00 ] [ 내일 06:00 ] [ 내일 07:00 ]
```

- `0700`(다음 07:00), `0801 0700`(8월 1일 07:00) 처럼 직접 입력해도 됩니다.
- **그때까지는 코레일에 아무 요청도 보내지 않습니다.** 미리 돌리는 것보다
  요청 수가 훨씬 적습니다.
- `/status` 로 확인하고 `/cancel` 로 취소합니다.
- 최대 **3일** 뒤까지만 예약됩니다. 저장된 로그인 정보의 보관 기한이 그때까지라,
  더 먼 시각은 막상 그때 로그인할 수단이 없습니다.
- 열차 출발 시각 이후로는 예약할 수 없습니다.
- 예정 시각에 봇이 꺼져 있었다면, 10분 이내에 살아나면 이어서 시작하고
  그보다 늦으면 시작하지 않고 알려줍니다. 몇 시간 전에 부탁한 검색이
  말없이 뒤늦게 도는 것보다 낫기 때문입니다.

## 결제

봇은 **예약까지만** 합니다. 결제는 코레일 앱이나 웹에서 직접 하셔야 합니다.
예약 후 약 10분 안에 결제하지 않으면 좌석이 풀립니다.

- 결제 재촉 알림은 **1분에 한 번** 옵니다.
- 예약 후 봇이 코레일에 **실제로 결제됐는지 확인**합니다. 결제가 확인되면
  알림을 멈추고, 기한이 지나도 미결제면 좌석을 잃었다고 알려줍니다.
  아무 메시지나 보내 알림을 멈춰도, 실제로 결제되지 않았다면 그 사실을
  따로 알려드립니다.

**결제 자동화는 하지 않습니다.** korail2 에 결제 기능 자체가 없고, 직접
구현하려면 카드 정보를 다뤄야 합니다. 지금은 최악의 유출이 코레일 계정
하나인데 그 범위가 통째로 달라집니다.

## 참고

- 본 서비스는 [carpedm20/korail2](https://github.com/carpedm20/korail2)를 기반으로 합니다.
- Dynapath 우회 패치가 적용된 [dhfhfk/korail2](https://github.com/dhfhfk/korail2/tree/bypassDynapath) fork를 패키지로 설치하여 사용합니다.

## 주의사항

1. 귀경길 기차 예매를 하지 못한 안타까운 영혼들을 위해 만든 프로그램이므로, 개인용 목적이 아닌 상업적 목적등으로 이용하는 것을 엄중히 금합니다.
2. 본 프로그램을 사용할 경우, 기본으로 설정된 1초에 1번 조회 요청에 대한 설정 값 이상으로 빠르게 설정하지 마십시오. 코레일 서버에 무리가 갈 뿐 아니라, 단위 시간내에 보다 빠른 값으로 조회를 요청할 경우, 계정이 정지될 수 있습니다.
3. 본 프로그램은 2026-04-08일 기준으로 정상 동작하지만, 사이트의 구성이나 변수명 변경등에 따라 언제든 동작하지 않을 수 있습니다.

## 설정법

### 로컬 개발 (macOS/Linux)

```bash
# 1. .env 생성 + 시크릿 발급 + 의존성 설치
./scripts/setup.sh

# 2. .env 를 열어 BOTTOKEN 입력

# 3. 개발용 Redis 기동 후 실행
./scripts/dev-redis.sh
./scripts/run.sh
```

### 업데이트 수신 방식 (`RECEIVE_MODE`)

| 값 | 동작 | 언제 |
|----|------|------|
| `polling` (기본) | 봇이 텔레그램에 직접 업데이트를 요청 | 공유기 뒤 라즈베리파이, 로컬 개발 등 외부에서 접근할 주소가 없을 때 |
| `webhook` | 텔레그램이 공개 HTTPS 엔드포인트로 전달 | 도메인과 인증서가 준비된 배포 환경 |

웹훅 모드일 때만 `TELEGRAM_WEBHOOK_SECRET` 과 웹훅 등록이 필요합니다.

```bash
# .env 에 RECEIVE_MODE=webhook 설정 후
./scripts/set-webhook.sh https://your.domain/telebot
```

봇 토큰 하나는 소비자를 하나만 가질 수 있습니다. 폴링은 시작할 때 등록된
웹훅을 해제하며, 같은 토큰으로 두 인스턴스를 띄우면 텔레그램이 409 를
돌려줍니다.

> compose 의 Redis 는 보안상 호스트에 포트를 열지 않습니다. 앱을 호스트에서
> 직접 실행할 때는 `dev-redis.sh` 가 띄우는 127.0.0.1 전용 인스턴스를
> 사용하세요.

**사용 가능한 명령어:** `make help` 또는 [scripts/README.md](scripts/README.md)

- `make setup` / `./scripts/setup.sh` - 개발 환경 설정
- `make run` / `./scripts/run.sh` - 애플리케이션 실행 (포그라운드)
- `make daemon` / `./scripts/run.sh --daemon` - 백그라운드 실행
- `make stop` / `./scripts/run.sh --stop` - 정지
- `make status` / `./scripts/status.sh` - 상태 확인
- `make test` / `./scripts/test.sh` - 테스트 실행
- `make secrets` / `./scripts/gen-secrets.sh` - 시크릿 발급 및 로테이션
- `make security-check` / `./scripts/security-check.sh` - 설정 보안 점검
- `make up` / `down` / `logs` - docker compose 스택 조작

### Docker 배포

```bash
# 1. Docker 이미지 빌드 (uv.lock 에서 바로 설치하므로 사전 생성 단계가 없습니다)
./scripts/docker-build.sh

# 2. .env 준비 후 스택 기동 (앱 + Redis)
./scripts/docker-up.sh
```

이미지는 멀티 스테이지로 빌드되며, 런타임 스테이지에는 `.venv` 만 들어갑니다
(`python:3.13-slim`, non-root 실행). 프로덕션 서버는 Flask 개발 서버가 아니라
waitress 이고, 폴러가 중복 기동되지 않도록 의도적으로 단일 프로세스 + 스레드로
동작합니다. CI 는 `linux/amd64` 와 `linux/arm64` 를 함께 빌드합니다.

`docker-compose.yml` 은 Redis 를 호스트로 노출하지 않고 `--requirepass` 로
띄웁니다. 따라서 `.env` 에 `REDIS_PASSWORD` 가 반드시 있어야 합니다
(`./scripts/gen-secrets.sh` 가 채워줍니다).

### 환경변수

| 변수명 | 필수 | 설명 |
|--------|------|------|
| `BOTTOKEN` | ✅ | 텔레그램 봇 토큰 |
| `RECEIVE_MODE` | ❌ | 업데이트 수신 방식. `polling`(기본) 또는 `webhook` |
| `TELEGRAM_WEBHOOK_SECRET` | ⚠️ | 웹훅 인증용 시크릿. 웹훅 모드에서는 없으면 앱이 기동하지 않습니다 |
| `SESSION_SECRET` | ⚠️ | 코레일 로그인 정보 암호화 키. 없으면 임시 키를 쓰며 재시작 시 세션이 무효화됩니다 |
| `REDIS_PASSWORD` | ⚠️ | Redis AUTH 비밀번호. docker compose 사용 시 필수 |
| `ADMIN_PASSWORD` | ❌ | 관리자 명령 비밀번호. 미설정 시 관리자 명령 전체 비활성화. **코레일 비밀번호와 달라야 합니다** |
| `ADMIN_MAGIC_STRING` | ❌ | 관리자 편의 로그인 트리거 문자열. 미설정 시 해당 기능 비활성화 |
| `USERID` / `USERPW` | ❌ | 서버에 설정하는 코레일 계정. 둘 다 채우면 휴대전화번호·비밀번호 입력 단계를 건너뛰고 이 계정으로 로그인합니다. 봇이 **단일 계정**이 되며 `ALLOW_LIST` 는 적용되지 않습니다 (전화번호를 받지 않으므로). 여러 명이 쓰는 봇이면 비워두세요 |
| `ALLOW_LIST` | ❌ | 허용할 사용자 전화번호 목록 (쉼표로 구분) |
| `SEARCH_INTERVAL` | ❌ | 코레일 요청 사이의 기본 대기 시간(초, 기본 1) |
| `SEARCH_INTERVAL_JITTER` | ❌ | 대기 시간 랜덤화 폭. 기본 `0.4` = 기본 간격의 ±40% 범위에서 매번 다시 뽑음. `0` 이면 고정 간격 |
| `RELOGIN_INTERVAL` | ❌ | 코레일 세션 갱신 주기(초, 기본 1800). `0` 이면 선제 갱신 없이 세션이 만료될 때만 재로그인 |
| `RELOGIN_INTERVAL_JITTER` | ❌ | 갱신 주기 랜덤화 폭. 기본 `0.4` = 로그인할 때마다 ±40% 범위에서 다시 뽑음 |
| `KORAIL_APP_VERSION` | ❌ | 클라이언트가 보고할 코레일 앱 빌드. 미설정 시 korail2 라이브러리 기본값. 코레일 앱 업데이트 직후 모든 요청이 실패하면 여기서 최신 빌드로 고정 |
| `SESSION_TTL_SECONDS` | ❌ | 세션 보관 기간 (기본 86400) |
| `RESUME_ON_RESTART` | ❌ | 재시작 시 중단된 검색 자동 재개 (기본 `true`). `SESSION_SECRET` 필요 |
| `RESUME_TTL_SECONDS` | ❌ | 재개용 자격증명 보관 상한 (기본 259200 = 3일) |
| `ADMIN_MAX_AUTH_FAILURES` | ❌ | 관리자 인증 실패 허용 횟수 (기본 5) |
| `ADMIN_LOCKOUT_SECONDS` | ❌ | 인증 차단 시간 (기본 900) |
| `FLASK_DEBUG` | ❌ | 기본 `False`. 외부에서 접근 가능한 호스트에서는 절대 켜지 마세요 |

전체 목록과 설명은 [.env.example](.env.example) 을 참고하세요.

## 보안

- **웹훅 인증**: 웹훅 모드에서 `/telebot` POST 는 Telegram 이 보내는
  `X-Telegram-Bot-Api-Secret-Token` 헤더를 검증합니다. 시크릿을 바꾸면
  `./scripts/set-webhook.sh` 로 재등록해야 합니다. 폴링 모드에서는 업데이트가
  우리가 연 아웃바운드 연결로만 들어오므로 이 경로가 아예 쓰이지 않습니다.
- **내부 콜백**: 백그라운드 예약 프로세스가 쓰는 `/telebot` GET 과
  `/check_payment` 는 루프백 주소 + 프로세스 시작 시 생성되는 토큰을 함께
  요구합니다.
- **로그인 정보**: 코레일 비밀번호는 Redis 에 암호화되어 저장되고, 예약
  프로세스에 넘긴 직후 세션에서 지워집니다. 자식 프로세스에는 argv 가 아닌
  stdin 으로 전달되므로 `ps` 에 노출되지 않습니다.
- **개인정보**: 코레일 ID 는 전화번호이므로 로그와 알림 메시지에서
  `010-****-5678` 형태로 마스킹됩니다. `/status` 는 본인 예약만 보여줍니다.
- **코레일 세션 격리**: korail2 는 HTTP 세션을 클래스 속성으로 들고 있어
  한 프로세스 안의 모든 클라이언트가 쿠키 하나를 공유합니다. 두 사용자가
  동시에 비밀번호를 입력하면 서로의 코레일 세션을 넘겨받을 수 있으므로,
  클라이언트를 만들 때마다 전용 세션을 붙여 분리합니다.
- **관리자 인증**: 실패 횟수가 `ADMIN_MAX_AUTH_FAILURES` 를 넘으면
  `ADMIN_LOCKOUT_SECONDS` 동안 차단됩니다.
- **재시작 복구**: 검색은 자식 프로세스에서 돌기 때문에 앱을 재시작하면
  기록만 남고 실제로 검색하는 주체가 사라집니다. 각 예약에 실행 ID를 남겨
  이전 실행의 잔재를 식별하고, 자동 재개하거나 사용자에게 알린 뒤
  정리합니다. 프로세스를 종료할 때는 `/proc` 으로 정말 우리 프로세스인지
  확인합니다 — 재활용된 PID 를 잘못 종료하는 사고를 막기 위함입니다.

  자동 재개(`RESUME_ON_RESTART=true`)를 켜면 검색이 살아있는 동안 코레일
  비밀번호가 **암호화된 전용 키**(`resume_credentials:{chat_id}`)에 보관되고,
  검색이 끝나거나 취소되면 즉시 삭제됩니다. 이미 좌석을 잡은 랜덤 배치
  검색은 중복 예약을 피하려고 자동 재개하지 않습니다.
- **종료 처리**: `Ctrl-C`, `docker stop`, 서비스 매니저의 `SIGTERM` 중 어느
  것으로 멈추든 앱이 자신이 띄운 검색 프로세스를 먼저 정리하고 나갑니다.
  검색 결과는 앱이 서빙하는 HTTP 엔드포인트로 돌아오기 때문에, 앱보다 오래
  사는 검색은 예매에 성공해도 알릴 곳이 없어 결제 없이 취소됩니다. 정리를
  거부하는 검색은 `SIGKILL` 로 확실히 종료합니다. Redis 의 기록은 남겨두므로
  다음 기동에서 위 재시작 복구가 그대로 이어받습니다.
- 배포 전 `./scripts/security-check.sh` 로 설정을 점검하세요.

## 개발 워크플로우

### 테스트 실행

```bash
# 전체 테스트 실행 (testcontainers 가 Redis 를 띄우므로 Docker 필요)
make test

# 단위 테스트만 (Redis 불필요)
make test-unit

# 특정 테스트만
./scripts/test.sh -k credential
```

**테스트 구성:**
- `tests/unit/` - 순수 단위 테스트 (입력 검증, 암호화, 마스킹, 자격증명 전달)
- `tests/integration/` - Redis 와 서비스 계층 통합 테스트
- `tests/e2e/` - 예약 플로우 전체 시나리오

### 코드 품질

```bash
make lint        # ruff format --check + ruff check
make format      # 포맷 및 자동 수정 적용
make typecheck   # mypy
```

`uv run pre-commit install` 을 한 번 실행해 두면 커밋 시점에 같은 검사가
자동으로 돕니다. CI 의 `check` 잡이 통과해야만 이미지 빌드와 배포가 진행됩니다.

### 의존성 추가 시
```bash
# 1. 추가 (pyproject.toml 과 uv.lock 이 함께 갱신됩니다)
uv add [패키지명]
uv add --dev [패키지명]     # 개발 전용

# 2. 커밋 — Docker 도 uv.lock 에서 바로 설치하므로 별도 생성 단계가 없습니다
git add pyproject.toml uv.lock
git commit -m "feat: Add new dependency"
```

### korail2 라이브러리 업데이트
```bash
# 포크의 브랜치를 다시 가리키게 하여 잠금 커밋을 갱신
uv lock --upgrade-package korail2
git add uv.lock
```

## 프로젝트 구조

```
src/korail_bot/                     # 설치 가능한 패키지 (src 레이아웃)
├── app.py                          # Flask 앱 진입점
├── config/                         # 설정 관리
├── models/                         # 데이터 모델
├── services/                       # 비즈니스 로직
├── storage/                        # 상태 관리
├── handlers/                       # 요청 처리
├── api/                            # API 엔드포인트
├── utils/                          # 유틸리티
└── telegramBot/                    # 레거시 코드

tests/                              # 테스트
```

패키지로 설치되므로 `PYTHONPATH` 를 손댈 필요가 없습니다. 임포트는 모두
`from korail_bot....` 형태입니다.

## 기술 스택

- **패키징**: [uv](https://docs.astral.sh/uv/) + `pyproject.toml` + `uv.lock`
- **Python**: 3.13
- **Backend**: Flask, Flask-RESTful, Flask-CORS
- **WSGI**: waitress (단일 프로세스 + 스레드)
- **Korail API**: [dhfhfk/korail2](https://github.com/dhfhfk/korail2/tree/bypassDynapath)
- **품질**: ruff (lint + format), mypy, pre-commit
- **Testing**: pytest, testcontainers
- **Deployment**: Docker (멀티 스테이지, amd64 + arm64), GitHub Actions
