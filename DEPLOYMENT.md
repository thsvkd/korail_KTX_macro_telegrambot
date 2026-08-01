# 배포 안내

기본 배포 방식은 이 저장소에서 이미지를 직접 빌드해 Docker Compose로 앱과 Redis를
함께 띄우는 것입니다. 공용 레지스트리 이미지는 제공하지 않습니다. 코레일
자격증명을 다루는 서비스이므로 직접 빌드했거나 직접 발행한 이미지만 사용하십시오.

## 서버 준비

필요한 것은 Git, Docker Engine, Docker Compose V2입니다. Linux에서 Docker 공식
설치 스크립트를 쓰는 예시는 다음과 같습니다.

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo apt-get update
sudo apt-get install docker-compose-plugin
sudo usermod -aG docker "$USER"
```

그룹 변경은 다시 로그인한 뒤 적용됩니다. 이후 저장소를 복제합니다.

```bash
git clone https://github.com/thsvkd/korail_KTX_macro_telegrambot.git
cd korail_KTX_macro_telegrambot
docker --version
docker compose version
```

## 최초 설정과 기동

`setup.sh --no-deps`는 `.env.example`을 `.env`로 복사하고 필요한 시크릿을
생성합니다. 서버에 uv를 설치하지 않았다면 아래처럼 `.env`를 직접 만들고
`setup.sh secrets --print`의 출력만 다른 환경에서 생성해 옮겨도 됩니다.

```bash
./scripts/setup.sh --no-deps

# .env에서 최소한 BOTTOKEN을 실제 값으로 바꿉니다.
# SESSION_SECRET, ADMIN_PASSWORD, REDIS_PASSWORD는 setup.sh가 생성합니다.
chmod 600 .env

# 배포 전에 플레이스홀더·빈 시크릿·디버그 설정을 검사합니다.
./scripts/setup.sh check

# 기본 이미지 태그 korailbot:local로 빌드하고 스택을 띄웁니다.
./scripts/deploy.sh build
./scripts/deploy.sh up
```

Compose는 다음 경계를 유지합니다.

- 앱 컨테이너 이름은 `korail_bot`, Redis는 `korail_redis`입니다.
- Redis는 `REDIS_PASSWORD`를 요구하고 Docker 네트워크 안에만 `6379`를
  노출합니다.
- 앱 HTTP 서버도 호스트 포트에 공개하지 않습니다. Telegram 업데이트는 long
  polling으로 받고, HTTP는 같은 앱 컨테이너의 검색 자식 프로세스가 내부 콜백을
  보낼 때만 사용합니다.
- 앱·Redis 컨테이너는 `restart: unless-stopped`로 다시 기동되고, Redis 데이터는
  별도 볼륨에 보존됩니다.

따라서 도메인, TLS 인증서, Telegram webhook, 공유기 포트포워딩과 앱·Redis용
방화벽 허용 규칙이 필요하지 않습니다.

## 운영 명령

가능하면 raw `docker compose`보다 저장소 스크립트를 사용하십시오. `.env` 검사와
안전 확인이 포함돼 있습니다.

```bash
# 상태와 로그
docker compose ps
./scripts/deploy.sh logs                 # 최근 100줄부터 계속 보기
./scripts/deploy.sh logs app --tail 50 --no-follow
./scripts/deploy.sh logs redis --no-follow

# Redis 상태와 데이터 규모
./scripts/status.sh redis PING
./scripts/status.sh redis INFO memory
./scripts/status.sh redis --keys

# 재기동
./scripts/deploy.sh down
./scripts/deploy.sh up

# 이미지 재빌드 후 앱 교체
git pull
./scripts/deploy.sh build
./scripts/deploy.sh up
```

`scripts/status.sh`의 기본 보고서는 호스트에서 직접 실행한 봇 프로세스를 위한
것입니다. Compose 배포의 프로세스 상태는 `docker compose ps`, 로그는
`scripts/deploy.sh logs`로 확인합니다. `scripts/status.sh redis ...`는 로컬
개발용과 Compose Redis를 모두 찾아 인증까지 처리합니다.

## 데이터 보존과 백업

일반적인 `down`과 재기동은 `redis_data` 볼륨을 보존합니다.

```bash
# RDB 스냅샷 생성
./scripts/status.sh redis BGSAVE

# 호스트로 복사
docker cp korail_redis:/data/dump.rdb ./dump.rdb
```

다음 명령은 복구하기 어려운 데이터를 지웁니다. 등록 계정, 진행·예약 검색,
결제 상태와 승인 정보가 함께 사라집니다.

```bash
# Redis DB 전체 삭제
./scripts/status.sh redis FLUSHDB

# 컨테이너와 Redis 볼륨 삭제 — 스크립트가 yes 확인을 요구합니다.
./scripts/deploy.sh down --volumes
```

실행 전에 백업과 정확한 대상 서버를 다시 확인하십시오.

## 직접 발행한 이미지 사용

레지스트리에 직접 이미지를 발행하려면 네임스페이스가 있는 태그를 사용합니다.

```bash
docker login
./scripts/deploy.sh push your-name/korailbot:latest
```

`push`는 빌드와 푸시 전에 `yes` 확인을 요구합니다. 서버의 `.env`에는 같은 태그를
지정합니다.

```dotenv
IMAGE_NAME=your-name/korailbot:latest
```

그 뒤 다음처럼 받아서 기동할 수 있습니다.

```bash
./scripts/deploy.sh up --pull
```

## GitHub Actions 자동 배포

`.github/workflows/cicd.yml`은 먼저 lint, 단위 테스트, 통합·E2E 테스트와 mypy를
실행합니다. mypy는 아직 `continue-on-error`이지만 나머지 검사가 실패하면 이미지를
발행하지 않습니다.

자동 발행·배포 job은 다음 조건을 모두 만족할 때만 실행됩니다.

- `pull_request`가 아닐 것
- `master` 브랜치일 것
- 저장소가 `thsvkd/korail_KTX_macro_telegrambot`일 것
- Repository variable `IMAGE_NAME`이 설정돼 있을 것

`DEPLOY_HOST`가 비어 있으면 amd64·arm64 이미지만 빌드해 푸시하고 서버 배포 단계는
건너뜁니다. 값이 있으면 Compose 파일과 `.env`를 서버에 배치하고 새 이미지를
기동합니다.

### Repository variables

| 이름 | 용도 |
| --- | --- |
| `IMAGE_NAME` | 빌드·푸시할 이미지 태그. 자동 발행을 켜는 필수 opt-in |
| `DEPLOY_HOST` | SSH 배포 대상. 비우면 이미지 발행까지만 수행 |

### Repository secrets

| 이름 | 용도 |
| --- | --- |
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | 레지스트리 로그인 |
| `SSH_USERNAME`, `SSH_PASSWORD` | 서버 복사·기동. `DEPLOY_HOST` 사용 시 필요 |
| `TELEGRAM_BOTTOKEN` | Telegram 봇 토큰 |
| `SESSION_SECRET` | 코레일 자격증명 암호화 키 재료 |
| `REDIS_PASSWORD` | Redis AUTH |
| `ADMIN_COMMAND_PASSWORD` | 관리자 명령 비밀번호 |
| `ALLOW_LIST` | 미리 승인할 전화번호 목록 |
| `ADMIN_USERID`, `ADMIN_PASSWD` | 개발자 방 고정 코레일 계정(선택) |
| `ADMIN_MAGIC_STRING` | 개발자 모드 전환 문자열(선택) |

Fork에서는 저장소 이름 가드 때문에 배포 job이 실행되지 않습니다. 자기 저장소에서
자동 배포하려면 workflow의 `github.repository` 조건을 자기 저장소로 바꾸고 필요한
변수·시크릿을 직접 구성해야 합니다.

## 점검과 문제 해결

### 앱이 재시작을 반복할 때

```bash
docker compose ps
./scripts/deploy.sh logs app --tail 100 --no-follow
docker compose config
./scripts/setup.sh check
```

흔한 원인은 비어 있거나 플레이스홀더인 `BOTTOKEN`, `SESSION_SECRET`,
`REDIS_PASSWORD`, 잘못된 Redis 연결 정보입니다.

### Telegram 409가 보일 때

같은 `BOTTOKEN`을 쓰는 봇 인스턴스가 둘 이상 long polling 중입니다. 다른 서버,
로컬 데몬, 이전 컨테이너를 확인하고 하나만 남기십시오.

```bash
docker compose ps
./scripts/run.sh --stop
```

앱은 시작할 때 예전에 등록된 webhook을 자동으로 지우므로 webhook 삭제 작업은
따로 필요하지 않습니다.

### Redis가 응답하지 않을 때

```bash
docker compose ps redis
./scripts/deploy.sh logs redis --tail 100 --no-follow
./scripts/status.sh redis PING
```

직접 `redis-cli`를 쓸 경우 인증이 필요합니다. 비밀번호가 프로세스 인자나 셸
기록에 남지 않도록 가능하면 `scripts/status.sh redis`를 사용하십시오.

### 배포 후 최소 확인

```bash
docker compose ps
./scripts/deploy.sh logs app --tail 50 --no-follow
./scripts/status.sh redis PING
```

로그에서 `Telegram poller started`를 확인한 뒤 테스트 계정으로 `/start`,
`/status`, `/cancel`을 확인합니다. 로그를 이슈나 CI에 붙일 때는 실제 전화번호와
Telegram `chat_id`를 반드시 가리십시오. 보안 문제는 공개 이슈가 아니라
[SECURITY.md](SECURITY.md)의 비공개 신고 경로를 사용합니다.
