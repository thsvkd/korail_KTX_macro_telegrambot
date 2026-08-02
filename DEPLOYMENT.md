# 배포 안내

이 봇은 Telegram 업데이트를 long polling으로 받습니다. 공개 IP, HTTPS, 포트
포워딩은 필요하지 않으며 Compose의 앱·Redis 포트도 호스트에 공개하지 않습니다.
기본 배포 방식은 현재 체크아웃에서 이미지를 직접 빌드해 앱과 Redis를 함께
실행하는 것입니다.

## 서버 준비

Git, Docker Engine, Docker Compose V2가 필요합니다.

```bash
git clone https://github.com/thsvkd/korail_KTX_macro_telegrambot.git
cd korail_KTX_macro_telegrambot
docker --version
docker compose version
```

## 운영 봇 최초 설정

```bash
./scripts/setup.sh --no-deps

# .env의 BOTTOKEN을 BotFather에서 받은 실제 운영 토큰으로 변경
./scripts/setup.sh check

# 현재 소스로 이미지 빌드 및 실행
./scripts/deploy.sh build
./scripts/deploy.sh up
./scripts/deploy.sh logs
```

`setup.sh`는 `.env.example`을 `.env`로 복사하고 `SESSION_SECRET`,
`ADMIN_PASSWORD`, `REDIS_PASSWORD`를 생성합니다. `.env` 권한은 `600`으로
유지하십시오.

개발자 채팅에서 서버 고정 계정을 쓰려면 다음 명령으로 코레일과 SRT 계정을
각각 설정할 수 있습니다.

```bash
./scripts/setup.sh --dev
```

출력된 `ADMIN_MAGIC_STRING`을 운영 봇에 보낸 채팅방만 고정 계정을 사용합니다.
일반 사용자는 각자 등록한 계정을 사용합니다.

## 운영 명령

```bash
# Compose 배포
./scripts/deploy.sh build
./scripts/deploy.sh up
./scripts/deploy.sh logs
./scripts/deploy.sh down

# 호스트에서 직접 실행
./scripts/server.sh start --daemon
./scripts/server.sh status
./scripts/server.sh logs -f
./scripts/server.sh restart
./scripts/server.sh stop
./scripts/server.sh redis stop

# 진단
./scripts/bootstrap.sh --check
./scripts/setup.sh check
./scripts/server.sh redis-cli --keys
```

`deploy.sh down --volumes`는 운영 Redis 볼륨을 삭제합니다. 모든 세션, 등록 계정,
예약 검색 상태가 사라지므로 초기화가 명확히 필요한 경우에만 사용하십시오.

## Telegram Mini App 배포

`webapp/`은 빌드 과정이 없는 정적 페이지입니다. `master`의 해당 파일이 바뀌면
`.github/workflows/pages.yml`이 GitHub Pages에 배포합니다. 저장소에서 처음 한
번은 **Settings → Pages → Source → GitHub Actions**를 선택해야 합니다. 이 fork의
기본 주소는 다음과 같습니다.

```text
https://thsvkd.github.io/korail_KTX_macro_telegrambot/
```

페이지가 실제로 열리는지 확인한 뒤 호스트의 `.env`에 아래 값을 추가합니다.

```bash
MINI_APP_URL=https://thsvkd.github.io/korail_KTX_macro_telegrambot/
```

봇을 재시작하면 `/start`의 답장 키보드가 이 HTTPS 페이지를 엽니다. Telegram
결과는 기존 long polling으로 들어오므로 `FLASK_HOST`를 외부에 열거나 webhook,
도메인, TLS 인증서를 앱 서버에 추가할 필요가 없습니다. 주소가 비어 있거나
HTTPS가 아니면 시작 로그에 비활성/경고가 남고 기존 채팅 예약만 동작합니다.

정적 화면은 철도 계정과 결제 정보를 받지 않으며 공개 API를 호출하지 않습니다.
GitHub Pages가 아닌 호스팅을 쓸 때도 `webapp/` 파일을 그대로 HTTPS로 제공하고
그 주소만 `MINI_APP_URL`에 넣으십시오.

## 배포 전 테스트 봇

BotFather에서 운영 봇과 다른 테스트 봇을 만든 뒤 격리된 `.env.test`를
생성합니다.

```bash
./scripts/setup.sh --test
./scripts/setup.sh check --test
```

Compose 테스트 서버:

```bash
./scripts/deploy.sh --test build
./scripts/deploy.sh --test up
./scripts/deploy.sh --test logs
./scripts/deploy.sh --test down
```

호스트에서 운영 봇과 함께 실행하는 테스트 서버:

```bash
./scripts/server.sh start --daemon --test
./scripts/server.sh status --test
./scripts/server.sh logs -f --test

# 테스트 런타임만 중지
./scripts/server.sh stop --test
./scripts/server.sh redis stop --test
```

호스트 실행은 선택한 포트의 Redis가 없으면 해당 런타임 전용 컨테이너를
자동으로 기동합니다. `server.sh redis [--test]`는 Redis만 별도로 관리할 때
사용합니다.

운영과 테스트는 다음 항목이 분리됩니다.

| 항목 | 운영 | 테스트 |
| --- | --- | --- |
| 환경 파일 | `.env` | `.env.test` |
| Telegram 토큰 | 운영 봇 토큰 | 별도 테스트 봇 토큰 |
| Compose 프로젝트 | 기본 프로젝트 | `korail-bot-test` |
| 앱 컨테이너 | `korail_bot` | `korail_bot_test` |
| Redis 컨테이너 | `korail_redis` | `korail_redis_test` |
| Redis 볼륨 | 운영 프로젝트 볼륨 | 테스트 프로젝트 볼륨 |
| 호스트 앱 포트 | 8080 | 8081 |
| 호스트 Redis | 127.0.0.1:6379 | 127.0.0.1:6380 |
| PID·로그 | `.run/korail-bot.*` | `.run/korail-bot-test.*` |

`deploy.sh --test up`과 `server.sh start --test`는 `.env`와 `.env.test`의
`BOTTOKEN`이 같으면 기동을 거부합니다. 토큰 하나를 두 poller가 사용하면
Telegram 409와 업데이트 유실이 발생하기 때문입니다.

테스트 기본값은 체험 검색 0회, 철도별 동시 검색 1개, 재시작 자동 재개 끔입니다.
설정 중 출력된 개발자 문구를 테스트 봇에 보내야 검색을 시작할 수 있습니다.

테스트 봇도 실제 코레일·SR 서버에 로그인하고 실제 예약을 만듭니다. 본인 계정만
사용하고 예약 성공을 확인한 뒤 즉시 취소하십시오. 두 봇의 요청은 철도사에서
같은 서버 IP로 보이므로 장시간 검색을 동시에 실행하지 마십시오.

## SRT 배포 전 확인

1. 코레일/SRT 사업자 선택 및 각 계정 로그인
2. 사업자별 역 목록과 SRT 열차 종류 질문 생략
3. 좌석 등급, 인원, 열차 선택과 뒤로가기
4. 검색 시작, 진행 알림과 `/cancel`
5. 실제 예약 성공 시 즉시 취소하고 철도사 예약 목록 확인
6. 로그에 `Unrecognised SR refusal` 경고가 있는지 확인

## 업데이트와 롤백

```bash
git pull --ff-only
./scripts/deploy.sh build
./scripts/deploy.sh up
./scripts/deploy.sh logs --tail 100 --no-follow
```

운영 배포 전에는 테스트 봇으로 같은 커밋을 먼저 검증하십시오. 문제가 생기면
정상 동작하던 커밋으로 이동해 이미지를 다시 빌드하고 `deploy.sh up`을 실행합니다.
Redis 볼륨은 `down --volumes`를 사용하지 않는 한 유지됩니다.

## 보안 점검

- `.env`와 `.env.test`를 커밋하지 않습니다.
- `ADMIN_PASSWORD`는 철도 계정 비밀번호와 다르게 설정합니다.
- `SESSION_SECRET`을 바꾸면 저장된 계정을 복호화할 수 없습니다.
- Redis와 앱 HTTP 포트를 외부에 공개하지 않습니다.
- Telegram 토큰이나 철도 계정을 로그·이슈·채팅에 붙여넣지 않습니다.
- 배포 전 `scripts/setup.sh check`와 `scripts/setup.sh check --test`를 실행합니다.

GitHub Actions 배포에 사용하는 `BOTTOKEN`, `SESSION_SECRET`, `REDIS_PASSWORD`,
`ADMIN_PASSWORD`, `SRT_ID`, `SRT_PW` 등은 저장소 파일이 아닌 GitHub Secrets에
보관합니다.
