# scripts

봇 운영에 필요한 스크립트 모음입니다. 모두 저장소 루트 기준으로 동작하며,
어디서 실행하든 상관없습니다.

```bash
scripts/setup.sh            # 최초 1회
scripts/dev-redis.sh        # 로컬 개발용 Redis
scripts/run.sh
```

웹훅 모드(`RECEIVE_MODE=webhook`)로 운영할 때만 웹훅 등록이 추가로 필요합니다.

```bash
scripts/set-webhook.sh https://your.domain/telebot
```

## 개발

| 스크립트 | 설명 |
| --- | --- |
| `setup.sh` | `.env` 생성, 시크릿 발급, 의존성 설치. `--no-deps` 로 의존성 설치 생략 |
| `gen-secrets.sh` | 비어 있는 시크릿 생성. `--print` 출력만, `--force` 재발급(로테이션) |
| `run.sh` | 로컬에서 봇 실행. Redis 연결 확인 후 기동. `--debug` 는 로그 레벨만 DEBUG |
| `dev-redis.sh` | 로컬 개발용 Redis 컨테이너(127.0.0.1:6379). `stop`, `status` 인자 지원 |
| `test.sh` | 테스트 실행. `tests/unit` 처럼 경로나 pytest 플래그를 그대로 전달 가능. Redis 는 testcontainers 가 띄우므로 Docker 필요 |
| `security-check.sh` | 설정 점검(시크릿 누락, 디버그 모드, Redis 노출, `.env` 커밋 여부) |

## Docker

| 스크립트 | 설명 |
| --- | --- |
| `docker-build.sh [tag]` | 로컬 소스로 이미지 빌드 (기본 `geunsam2/korailbot:dev`) |
| `docker-up.sh [service]` | 스택 기동. `--pull` 이미지 갱신, `--foreground` 로그 스트리밍 |
| `docker-down.sh` | 스택 중지. `--volumes` 는 Redis 볼륨까지 삭제(확인 프롬프트) |
| `docker-logs.sh [service]` | 로그 follow. `--tail N`, `--no-follow` |
| `docker-push.sh [tag]` | 이미지 빌드 후 레지스트리 푸시(확인 프롬프트) |
| `redis-cli.sh` | 실행 중인 Redis 접속. `--keys` 는 키 스페이스 요약 |

## 텔레그램

| 스크립트 | 설명 |
| --- | --- |
| `set-webhook.sh <url>` | `.env` 의 `TELEGRAM_WEBHOOK_SECRET` 과 함께 웹훅 등록 |
| `set-webhook.sh --info` | 현재 웹훅 상태 조회 |
| `set-webhook.sh --delete` | 웹훅 해제 |

## 알아둘 점

- **`RECEIVE_MODE` 이 업데이트 수신 방식을 정합니다.** 기본값 `polling` 은
  봇이 텔레그램에 직접 물어보는 방식이라 공인 IP·HTTPS·포트포워딩이 필요
  없습니다(공유기 뒤 라즈베리파이). `webhook` 은 텔레그램이 공개 HTTPS
  엔드포인트로 밀어넣는 방식입니다. 봇 토큰 하나당 소비자는 하나뿐이라,
  폴링 시작 시 등록된 웹훅은 자동으로 해제됩니다.
- **웹훅 모드에서는 웹훅 시크릿이 필수입니다.** `TELEGRAM_WEBHOOK_SECRET`
  없이는 앱이 기동하지 않습니다. 이 값이 없으면 `/telebot` 에 도달할 수 있는
  누구나 임의 사용자의 메시지를 위조할 수 있기 때문입니다. 값을 바꾸면
  `set-webhook.sh` 를 다시 실행해야 합니다. 폴링 모드에서는 노출되는
  엔드포인트가 없으므로 이 값을 요구하지 않습니다.
- **`SESSION_SECRET` 을 바꾸면** 저장된 세션을 복호화할 수 없어 사용자들이
  코레일 로그인 정보를 다시 입력해야 합니다.
- **`ADMIN_PASSWORD` 는 코레일 비밀번호와 달라야 합니다.** 텔레그램으로
  추측 시도가 가능한 값이므로, 뚫렸을 때 코레일 계정까지 넘어가면 안 됩니다.
- `docker-up.sh` 는 `REDIS_PASSWORD` 가 비어 있으면 기동을 거부합니다.
  compose 가 Redis 를 `--requirepass` 로 띄우기 때문입니다.
- **compose 의 Redis 는 호스트에 포트를 열지 않습니다.** 앱을 호스트에서
  직접 실행하는 `run.sh` 는 그래서 `dev-redis.sh` 가 띄우는 별도 인스턴스
  (127.0.0.1 바인딩)를 사용합니다.
- `test.sh` 는 통합·e2e 테스트에서만 testcontainers 로 Redis 를 띄웁니다.
  `test.sh tests/unit` 은 Redis 를 전혀 쓰지 않으므로 Docker 없이 돕니다.
- 인터프리터는 `uv` 가 관리합니다. `pyproject.toml` 의 `requires-python`
  (3.13) 에 맞는 버전을 호스트에 없으면 직접 내려받으므로, 별도로 파이썬을
  설치해 둘 필요가 없습니다. Docker 이미지도 같은 3.13 으로 빌드됩니다.
- 배포 서버에 물린 `docker-compose.yml` 은 Docker Hub 의
  `geunsam2/korailbot:latest` 를 받아서 실행합니다. 로컬에서 빌드한 이미지를
  쓰려면 태그를 맞추거나 compose override 를 두세요.
