# 코레일 KTX 예매 텔레그램 챗봇

매진된 KTX 열차를 자동으로 모니터링하여 좌석이 나오면 예약해주는 텔레그램 봇입니다.

## 빠른 시작

```bash
# 프로젝트 클론
git clone https://github.com/GeunSam2/korail_KTX_macro_telegrambot.git
cd korail_KTX_macro_telegrambot

# .env 생성 + 시크릿 발급 + 의존성 설치 (처음 한 번만)
./scripts/setup.sh

# .env 에 BOTTOKEN 입력

# 실행 (기본값인 폴링 모드는 공인 IP·HTTPS 없이 바로 동작합니다)
./scripts/run.sh
```

스크립트 전체 목록은 [scripts/README.md](scripts/README.md)를 참고하세요.

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
- `make run` / `./scripts/run.sh` - 애플리케이션 실행
- `make test` / `./scripts/test.sh` - 테스트 실행
- `make secrets` / `./scripts/gen-secrets.sh` - 시크릿 발급 및 로테이션
- `make security-check` / `./scripts/security-check.sh` - 설정 보안 점검
- `make up` / `down` / `logs` - docker compose 스택 조작

### Docker 배포

```bash
# 1. requirements.txt 생성 (Pipfile에서)
make requirements

# 2. Docker 이미지 빌드
./scripts/docker-build.sh

# 3. .env 준비 후 스택 기동 (앱 + Redis)
./scripts/docker-up.sh
```

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
| `USERID` / `USERPW` | ❌ | 관리자 편의 로그인용 코레일 계정 |
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

### 의존성 추가 시
```bash
# 1. Pipfile에 패키지 추가
pipenv install [패키지명]

# 2. requirements.txt 재생성 (Docker 배포용)
make requirements

# 3. 커밋
git add Pipfile Pipfile.lock requirements.txt
git commit -m "feat: Add new dependency"
```

### korail2 라이브러리 업데이트
```bash
# 최신 버전으로 업데이트
pipenv update korail2

# requirements.txt 재생성
make requirements
```

## 프로젝트 구조

```
src/
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

## 기술 스택

- **Backend**: Flask, Flask-RESTful, Flask-CORS
- **Telegram**: python-telegram-bot
- **Korail API**: [dhfhfk/korail2](https://github.com/dhfhfk/korail2/tree/bypassDynapath)
- **Testing**: pytest
- **Deployment**: Docker
