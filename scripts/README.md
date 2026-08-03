# scripts

여섯 개의 공개 스크립트가 각각 하나의 역할을 맡습니다. 모든 스크립트는 어느
디렉터리에서 호출해도 저장소 루트를 기준으로 동작합니다.

| 스크립트 | 역할 | 반복 실행 |
| --- | --- | --- |
| `bootstrap.sh` | 의존성 설치·갱신 | 안전 (멱등) |
| `setup.sh` | 최초 1회 초기화 — `.env`, 시크릿, 선택지 | 질문을 다시 함 |
| `server.sh` | 봇 기동·정지·상태·로그 (compose 스택) | 안전 |
| `deploy.sh` | 이미지 빌드·발행과 스택 조작 | 안전 |
| `migrate-redis.sh` | 예전 Redis 데이터를 스택으로 이관 | 안전 (덮어쓰기는 거부) |
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
scripts/server.sh start          # 앱 + Redis 컨테이너 기동 (없으면 이미지도 빌드)
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
| `server.sh` | `start [--foreground] [--build] [--debug]` | 스택 기동 (기본은 백그라운드) |
| | `stop [--remove]` | 컨테이너 정지, `--remove`면 삭제까지 (데이터는 유지) |
| | `restart [--build] [--debug]` | 앱 컨테이너만 새로 생성 |
| | `status [--log N]` | 컨테이너·설정·연결·검색 상태 보고 |
| | `logs [N] [-f] [app\|redis]` | 컨테이너 로그 출력 |
| | `redis [start\|stop\|status]` | Redis 서비스만 따로 관리 |
| | `redis-cli [--keys\|COMMAND ...]` | 봇이 쓰는 Redis 조회 및 CLI 접속 |
| | `... --test` | 위 전부를 스테이징 봇 대상으로 |
| | `... --host` | 컨테이너 대신 `.venv` 프로세스 대상으로 (디버깅용) |
| `deploy.sh` | `[--test] build [tag]` | 선택한 Docker 이미지 빌드 |
| | `[--test] up [service] [--pull] [--foreground]` | 선택한 Compose 스택 기동 |
| | `[--test] down [--purge-data]` | 스택 중지, `--purge-data`면 Redis 데이터까지 삭제 |
| | `[--test] logs [service] [--tail N] [--no-follow]` | 선택한 Compose 로그 출력 |
| | `[--test] push <tag>` | 선택한 이미지 빌드 후 레지스트리 푸시 |
| `test.sh` | `[pytest 인자...]` | 테스트 실행 |
| `migrate-redis.sh` | `[--test] [--from-container N\|--from-volume N] [--force]` | 예전 Redis 데이터를 `REDIS_DATA_DIR`로 이관 |
| `lint.sh` | `[--fix] [--all]` | ruff 검사, 자동 수정, mypy·shellcheck 추가 |

`server.sh status`는 봇이 살아 있으면 0, 아니면 1로 끝나므로 다른 명령의
조건으로 쓸 수 있습니다.

```bash
scripts/server.sh status >/dev/null || scripts/server.sh start
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
- 앱 포트 8081(컨테이너 내부), Redis 데이터 `./.data/redis-test`
- 별도 PID 파일과 로그 파일
- 체험 검색 0회, 철도별 동시 검색 1개, 자동 재개 끔

운영 봇과 동시에 실행:

```bash
scripts/server.sh start --test
scripts/server.sh status --test
scripts/server.sh logs -f --test
scripts/server.sh stop --test
```

테스트 스택과 운영 스택의 토큰이 같으면 기동을 거부합니다. Compose 프로젝트,
컨테이너, 네트워크와 Redis 데이터 디렉터리도 분리되며, long polling 방식이므로
앱 HTTP 포트는 어느 스택에서도 호스트에 공개하지 않습니다.

## 알아둘 점

- Telegram 업데이트는 long polling으로 받으며 시작할 때 과거 webhook을
  자동으로 해제합니다.
- Redis 데이터는 `REDIS_DATA_DIR`(기본 `./.data/redis`)에 바인드 마운트됩니다.
  컨테이너를 멈추거나 지워도, `down --volumes`나 `docker volume prune`을 해도
  등록 계정과 검색 상태는 남습니다. 지우려면 `deploy.sh down --purge-data`.
- `--host` 실행은 loopback Redis가 없으면 `server.sh`가 127.0.0.1 전용
  개발용 컨테이너를 자동으로 기동합니다. 스택의 데이터와는 별개입니다.
- `--host` 실행에서 봇 프로세스는 명령줄과 런타임 프로파일로 찾습니다. 어느
  스크립트가 띄웠는지는 보지 않으므로 예전 스크립트로 띄운 봇도 그대로
  정지되고, 컨테이너 안의 프로세스는 마운트 네임스페이스로 걸러냅니다.
- `server.sh status`는 다른 worktree에서 실행해도 발견한 봇 PID의 Redis
  설정을 사용하므로 운영 `.env`와 테스트 `.env.test`를 섞지 않습니다.
- `SESSION_SECRET`을 바꾸면 저장된 철도 계정을 다시 등록해야 합니다.
- `ADMIN_PASSWORD`는 코레일/SRT 비밀번호와 다르게 설정해야 합니다.
- `test.sh tests/unit`은 Docker 없이 실행할 수 있습니다. 통합·E2E 테스트는
  testcontainers가 Redis를 띄우므로 Docker가 필요합니다.
- Compose는 공용 이미지를 내려받지 않고 현재 소스에서 빌드한
  `korailbot:local` 또는 테스트용 `korailbot:test`를 사용합니다.
