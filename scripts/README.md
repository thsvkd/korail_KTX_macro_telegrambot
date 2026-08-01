# scripts

설정·실행·상태·테스트·배포를 다섯 개의 공개 스크립트로 관리합니다. 모든
스크립트는 어느 디렉터리에서 호출해도 저장소 루트를 기준으로 동작합니다.

## 처음 시작하기

```bash
scripts/setup.sh                 # .env, 시크릿, 의존성 준비
scripts/run.sh                   # Redis가 없으면 자동으로 준비하고 운영 봇 실행
```

안내를 따라 처음 설정하려면 다음 명령을 사용합니다.

```bash
scripts/setup.sh onboarding
```

## 명령

| 스크립트 | 명령 | 설명 |
| --- | --- | --- |
| `setup.sh` | `[--no-deps] [--dev]` | 운영 `.env`, 시크릿, 의존성 및 개발자 채팅 준비 |
| | `--test` | 격리된 테스트 봇 `.env.test` 준비 |
| | `onboarding [--reset]` | 대화형 최초 설정 |
| | `secrets [--test] [--print\|--force]` | 운영 또는 테스트 시크릿 생성·로테이션 |
| | `check [--test]` | 운영 또는 테스트 설정 보안 점검 |
| `run.sh` | `[--test] [--daemon\|--stop\|--debug]` | 선택한 호스트 봇 실행·정지 |
| | `[--test] redis [start\|stop\|status]` | 선택한 호스트 Redis 관리 |
| `status.sh` | `[--test] [--log N]` | 프로세스·포트·Redis·검색 상태 보고 |
| | `[--test] logs [N] [-f]` | 선택한 로컬 데몬 로그 출력 |
| | `[--test] redis [--keys\|COMMAND ...]` | 선택한 Redis 조회 및 CLI 접속 |
| `test.sh` | `[pytest 인자...]` | 테스트 실행 |
| `deploy.sh` | `[--test] build [tag]` | 선택한 Docker 이미지 빌드 |
| | `[--test] up [service] [--pull] [--foreground]` | 선택한 Compose 스택 기동 |
| | `[--test] down [--volumes]` | 선택한 Compose 스택 중지 |
| | `[--test] logs [service] [--tail N] [--no-follow]` | 선택한 Compose 로그 출력 |
| | `[--test] push <tag>` | 선택한 이미지 빌드 후 레지스트리 푸시 |

`_common.sh`는 위 스크립트가 공유하는 내부 함수 모음이며 직접 실행하지 않습니다.

## 운영과 테스트 격리

`setup.sh --test`는 다음 기본값을 `.env.test`에 기록합니다.

- 운영과 다른 Telegram 봇 토큰 및 암호화 시크릿
- Compose 프로젝트 `korail-bot-test`
- 컨테이너 `korail_bot_test`, `korail_redis_test`
- 호스트 앱 포트 8081, 호스트 Redis 6380
- 별도 PID 파일과 로그 파일
- 체험 검색 0회, 철도별 동시 검색 1개, 자동 재개 끔

Compose 실행:

```bash
scripts/deploy.sh --test build
scripts/deploy.sh --test up
scripts/deploy.sh --test logs
scripts/deploy.sh --test down
```

호스트에서 운영 봇과 동시에 실행:

```bash
scripts/run.sh --test --daemon
scripts/status.sh --test
scripts/run.sh --test --stop
scripts/run.sh --test redis stop
```

`run.sh --test`는 6380 포트의 전용 Redis가 없으면 자동으로 기동합니다.
`run.sh --test redis` 하위 명령은 Redis만 미리 시작하거나 상태를 확인하고
중지할 때 사용합니다.

테스트 스택과 운영 스택의 토큰이 같으면 기동을 거부합니다. Compose 프로젝트,
컨테이너, 네트워크와 Redis 볼륨도 분리되며, long polling 방식이므로 앱 HTTP
포트는 어느 Compose 스택에서도 호스트에 공개하지 않습니다.

## 알아둘 점

- Telegram 업데이트는 long polling으로 받으며 시작할 때 과거 webhook을
  자동으로 해제합니다.
- 호스트 실행은 loopback Redis가 없으면 `run.sh`가 127.0.0.1 전용
  컨테이너를 자동으로 기동합니다.
- `status.sh`는 다른 worktree에서 실행해도 발견한 봇 PID의 Redis 설정을
  사용하므로 운영 `.env`와 테스트 `.env.test`를 섞지 않습니다.
- `SESSION_SECRET`을 바꾸면 저장된 철도 계정을 다시 등록해야 합니다.
- `ADMIN_PASSWORD`는 코레일/SRT 비밀번호와 다르게 설정해야 합니다.
- `test.sh tests/unit`은 Docker 없이 실행할 수 있습니다. 통합·E2E 테스트는
  testcontainers가 Redis를 띄우므로 Docker가 필요합니다.
- Compose는 공용 이미지를 내려받지 않고 현재 소스에서 빌드한
  `korailbot:local` 또는 테스트용 `korailbot:test`를 사용합니다.
