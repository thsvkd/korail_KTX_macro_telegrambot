# scripts

여섯 개의 공개 스크립트가 각각 하나의 역할을 맡습니다. 모든 스크립트는 어느
디렉터리에서 호출해도 저장소 루트를 기준으로 동작합니다.

| 스크립트 | 역할 | 반복 실행 |
| --- | --- | --- |
| `bootstrap.sh` | 의존성 설치·갱신 | 안전 (멱등) |
| `setup.sh` | 최초 1회 초기화 — `.env`, 시크릿, 선택지 | 질문을 다시 함 |
| `server.sh` | 호스트에서 봇 기동·정지·상태·로그 | 안전 |
| `deploy.sh` | Docker Compose 스택 | 안전 |
| `test.sh` | 테스트 실행 | 안전 |
| `lint.sh` | 포맷·린트 검사 | 안전 |

`_common.sh`는 위 스크립트가 공유하는 내부 함수 모음이며 직접 실행하지
않습니다.

`bootstrap.sh`와 `setup.sh`를 나눈 이유는 하나입니다. 의존성 설치는 언제
몇 번을 해도 같은 결과를 내지만, 설정은 파일을 만들고 사람에게 질문합니다.
뒤엣것을 앞엣것에 섞어두면 `git pull` 뒤에 의존성만 맞추려다 `.env`를
건드리게 됩니다.

## 처음 시작하기

```bash
scripts/setup.sh                 # .env, 시크릿, 의존성 준비
scripts/server.sh start          # Redis가 없으면 자동으로 준비하고 봇 실행
```

안내를 따라 처음 설정하려면 다음 명령을 사용합니다.

```bash
scripts/setup.sh onboarding
```

## 명령

| 스크립트 | 명령 | 설명 |
| --- | --- | --- |
| `bootstrap.sh` | `[--quiet]` | `uv.lock` 대로 `.venv` 설치·갱신 |
| | `--check` | 무엇이 없는지 보고만 하고 아무것도 바꾸지 않음 |
| `setup.sh` | `[--no-deps] [--dev]` | 운영 `.env`, 시크릿, 의존성 및 개발자 채팅 준비 |
| | `--test` | 격리된 테스트 봇 `.env.test` 준비 |
| | `onboarding [--reset]` | 대화형 최초 설정 |
| | `secrets [--test] [--print\|--force]` | 운영 또는 테스트 시크릿 생성·로테이션 |
| | `check [--test]` | 운영 또는 테스트 설정 보안 점검 |
| `server.sh` | `start [--daemon] [--debug]` | 봇 실행 (기본은 포그라운드) |
| | `stop` | 실행 중인 봇 정지 |
| | `restart [--foreground] [--debug]` | 정지 후 데몬으로 재시작 |
| | `status [--log N]` | 프로세스·포트·Redis·검색 상태 보고 |
| | `logs [N] [-f]` | 데몬 로그 출력 |
| | `redis [start\|stop\|status]` | 호스트 개발용 Redis 관리 |
| | `redis-cli [--keys\|COMMAND ...]` | 봇이 쓰는 Redis 조회 및 CLI 접속 |
| | `... --test` | 위 전부를 스테이징 봇 대상으로 |
| `deploy.sh` | `[--test] build [tag]` | 선택한 Docker 이미지 빌드 |
| | `[--test] up [service] [--pull] [--foreground]` | 선택한 Compose 스택 기동 |
| | `[--test] down [--volumes]` | 선택한 Compose 스택 중지 |
| | `[--test] logs [service] [--tail N] [--no-follow]` | 선택한 Compose 로그 출력 |
| | `[--test] push <tag>` | 선택한 이미지 빌드 후 레지스트리 푸시 |
| `test.sh` | `[pytest 인자...]` | 테스트 실행 |
| `lint.sh` | `[--fix] [--all]` | ruff 검사, 자동 수정, mypy·shellcheck 추가 |

`server.sh status`는 봇이 살아 있으면 0, 아니면 1로 끝나므로 다른 명령의
조건으로 쓸 수 있습니다.

```bash
scripts/server.sh status >/dev/null || scripts/server.sh start --daemon
```

`restart`는 새 프로세스가 뜰 수 있다는 것이 확인된 뒤에야 기존 프로세스를
내립니다. 올라오지 못할 재시작이 멀쩡히 돌던 봇을 죽이지 않습니다.

## Makefile

`make`는 위 스크립트의 얇은 껍데기입니다. 스테이징 봇을 대상으로 하려면
타겟마다 별도 이름을 두는 대신 `TEST=1`을 붙입니다.

```bash
make status            # 운영 봇
make status TEST=1     # .env.test 의 스테이징 봇
```

전체 목록은 `make help`로 확인합니다.

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
scripts/server.sh start --daemon --test
scripts/server.sh status --test
scripts/server.sh stop --test
scripts/server.sh redis stop --test
```

테스트 스택과 운영 스택의 토큰이 같으면 기동을 거부합니다. Compose 프로젝트,
컨테이너, 네트워크와 Redis 볼륨도 분리되며, long polling 방식이므로 앱 HTTP
포트는 어느 Compose 스택에서도 호스트에 공개하지 않습니다.

## 알아둘 점

- Telegram 업데이트는 long polling으로 받으며 시작할 때 과거 webhook을
  자동으로 해제합니다.
- 호스트 실행은 loopback Redis가 없으면 `server.sh`가 127.0.0.1 전용
  컨테이너를 자동으로 기동합니다.
- 봇 프로세스는 명령줄과 런타임 프로파일로 찾습니다. 어느 스크립트가
  띄웠는지는 보지 않으므로, 예전 스크립트로 띄운 봇도 그대로 정지됩니다.
- `server.sh status`는 다른 worktree에서 실행해도 발견한 봇 PID의 Redis
  설정을 사용하므로 운영 `.env`와 테스트 `.env.test`를 섞지 않습니다.
- `SESSION_SECRET`을 바꾸면 저장된 철도 계정을 다시 등록해야 합니다.
- `ADMIN_PASSWORD`는 코레일/SRT 비밀번호와 다르게 설정해야 합니다.
- `test.sh tests/unit`은 Docker 없이 실행할 수 있습니다. 통합·E2E 테스트는
  testcontainers가 Redis를 띄우므로 Docker가 필요합니다.
- Compose는 공용 이미지를 내려받지 않고 현재 소스에서 빌드한
  `korailbot:local` 또는 테스트용 `korailbot:test`를 사용합니다.
