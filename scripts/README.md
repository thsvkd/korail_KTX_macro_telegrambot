# scripts

봇의 설정·실행·진단·테스트·배포를 다섯 개의 명령으로 관리합니다. 모든
스크립트는 저장소 루트를 기준으로 동작하므로 어느 디렉터리에서 실행해도 됩니다.

## 처음 시작하기

```bash
scripts/setup.sh                 # .env, 시크릿, 의존성 준비
scripts/run.sh redis             # 로컬 개발용 Redis 시작
scripts/run.sh                   # 봇 실행
```

처음부터 안내받으며 설정하려면 `scripts/setup.sh onboarding`을 사용합니다.

## 명령

| 스크립트 | 명령 | 설명 |
| --- | --- | --- |
| `setup.sh` | `[--no-deps] [--dev]` | 기본 설정 및 개발자 채팅방 준비 |
| | `onboarding [--reset]` | 대화형 최초 설정 |
| | `secrets [--print\|--force]` | 시크릿 생성 또는 로테이션 |
| | `check` | 설정과 배포 보안 점검 |
| `run.sh` | `[--daemon\|--stop\|--debug]` | 로컬 봇 실행 및 정지 |
| | `redis [start\|stop\|status]` | 로컬 개발용 Redis 관리 |
| `status.sh` | `[--log N]` | 프로세스·포트·Redis·검색 상태 보고 |
| | `logs [N] [-f]` | 로컬 데몬 로그 출력 |
| | `redis [--keys\|COMMAND ...]` | 실행 중인 Redis 조회 및 CLI 접속 |
| `test.sh` | `[pytest 인자...]` | 테스트 실행 |
| `deploy.sh` | `build [tag]` | Docker 이미지 빌드 |
| | `up [service] [--pull] [--foreground]` | Compose 스택 기동 |
| | `down [--volumes]` | Compose 스택 정지 |
| | `logs [service] [--tail N] [--no-follow]` | Compose 로그 출력 |
| | `push <tag>` | 이미지 빌드 후 레지스트리 푸시 |

`_common.sh`는 위 명령들이 공유하는 내부 함수 모음이며 직접 실행하지 않습니다.

## 알아둘 점

- Telegram 업데이트는 long polling으로 받습니다. 공개 IP·HTTPS·포트포워딩이
  필요하지 않습니다. 시작할 때 과거에 등록된 webhook은 자동 해제합니다.
- `SESSION_SECRET`을 바꾸면 저장된 세션을 복호화할 수 없어 사용자가 코레일
  로그인 정보를 다시 입력해야 합니다.
- `ADMIN_PASSWORD`는 코레일 비밀번호와 다르게 설정해야 합니다.
- Compose의 Redis는 호스트에 포트를 열지 않습니다. 호스트에서 직접 실행하는
  `run.sh`는 `run.sh redis`가 띄우는 127.0.0.1 전용 Redis를 사용합니다.
- `test.sh tests/unit`은 Docker가 필요 없지만 통합·E2E 테스트는 testcontainers로
  Redis를 띄우므로 Docker가 필요합니다.
- 인터프리터와 의존성은 `uv` 및 `uv.lock`으로 관리합니다.
