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

봇과 Redis는 모두 컨테이너로 뜹니다. `server.sh`가 그 스택을 다룹니다.

```bash
# 일상 운영 (docker compose)
./scripts/server.sh start            # 앱 + Redis 기동 (없으면 이미지도 빌드)
./scripts/server.sh status           # 컨테이너·설정·연결·진행 중인 검색
./scripts/server.sh logs -f
./scripts/server.sh restart          # 앱 컨테이너만 새로 만든다
./scripts/server.sh restart --build  # 코드가 바뀌었을 때
./scripts/server.sh stop             # 컨테이너만 멈춘다 (데이터 유지)
./scripts/server.sh stop --remove    # 컨테이너·네트워크까지 제거 (데이터 유지)

# 이미지만 다루기
./scripts/deploy.sh build
./scripts/deploy.sh up
./scripts/deploy.sh down

# 진단
./scripts/setup.sh check
./scripts/server.sh redis-cli --keys

# 로컬 인터프리터로 디버깅 (배포된 데이터와 분리된 개발용 Redis)
./scripts/server.sh start --host --foreground
```

Docker 데몬이 뜨면 `restart: unless-stopped`가 스택을 다시 올리므로 재부팅 후
따로 할 일은 없습니다.

## Redis 데이터가 있는 곳

`REDIS_DATA_DIR`(기본 `./.data/redis`, 테스트 봇은 `./.data/redis-test`)를 컨테이너
`/data`에 그대로 마운트합니다. 이름 있는 볼륨이 아니므로 컨테이너를 멈추든 지우든,
`docker compose down --volumes`나 `docker volume prune`을 하든 등록 계정·세션·예약
검색 상태는 남습니다. 지우는 방법은 그 디렉터리를 지우는 것뿐입니다.

```bash
./scripts/deploy.sh down --purge-data   # 확인을 받고 데이터까지 삭제
```

파일은 Redis 컨테이너의 uid 소유라 호스트에서 바로 읽고 쓸 수 없습니다. 백업은
컨테이너를 통해 하십시오.

```bash
docker run --rm -v "$PWD/.data/redis:/data:ro" -v "$PWD:/out" \
  redis:7-alpine sh -c 'cp -a /data /out/redis-backup-$(date +%F)'
```

## 예전 방식에서 옮겨오기

호스트에서 직접 실행하며 단독 Redis 컨테이너(`korail_dev_redis`)를 쓰던 설치나,
Redis 데이터가 이름 있는 볼륨에 있던 설치는 한 번만 옮기면 됩니다.

```bash
./scripts/server.sh stop --host        # 호스트 프로세스를 먼저 내린다
./scripts/migrate-redis.sh             # 스냅숏을 REDIS_DATA_DIR 로 복사
./scripts/server.sh start
```

`migrate-redis.sh`는 원본을 지우지 않습니다. 봇이 정상 동작하는 것을 확인한 뒤
직접 `docker rm -f korail_dev_redis` 로 정리하십시오. 원본을 자동으로 찾지 못하면
`--from-container NAME` 이나 `--from-volume NAME` 으로 지정할 수 있습니다.

## Telegram Mini App 배포

미니 앱은 예약 전 과정을 한 화면에서 처리합니다 — 조건 입력, 실시간 열차 목록과
선택, 검색 시작과 시작 시각 예약, 진행 상황, 결제 대기 예약 취소, 계정 등록,
즐겨찾기, 알림 설정. 화면은 얇은 클라이언트이고 코레일·SR 로직은 전부 서버에
그대로 있습니다. 화면이 하는 일은 봇의 API를 호출하고 받은 JSON을 그리는 것뿐입니다.

그래서 정적 호스팅만으로는 동작하지 않습니다. **봇이 페이지와 API를 같은
오리진으로 직접 서빙**하고, 그 하나를 인터넷에 노출합니다.

### 1. 봇에서 API 켜기

```bash
MINI_APP_API_ENABLED=true
MINI_APP_API_PORT=8081
MINI_APP_URL=https://<아래에서 정한 주소>/
```

`MINI_APP_API_PORT`는 `FLASK_PORT`와 **반드시 달라야 합니다.** 봇은 리스너를 둘
띄웁니다. 내부 리스너(`FLASK_PORT`)에는 검색 프로세스가 결과를 보고하는
`/reservation-callback`과 `/check_payment`가 있고, 공개 리스너에는 미니 앱 API와
페이지만 있습니다. 두 값이 같으면 시작 로그가 경고합니다.

**이 분리가 이 구조의 핵심입니다.** `/reservation-callback`은 임의의 채팅방에
임의의 메시지를 보낼 수 있고, 자신을 지키는 수단은 "요청이 루프백에서 왔는가"
하나뿐입니다. 역프록시를 앞에 두는 순간 그 검사는 무너집니다 — 프록시는 로컬에서
앱에 연결하므로 인터넷에서 온 요청도 루프백으로 보입니다. 그래서 검사를 고치는
대신 **그 경로를 노출되는 소켓에 아예 올리지 않습니다.**

compose는 이 포트를 호스트의 루프백에만 매답니다. 인터넷에 내보내는 것은 앞단의
역프록시입니다.

### 2. Tailscale 사이드카로 주소 만들기

compose 스택에는 `tailscale` 서비스가 있고, 이것이 **자기 이름을 가진 노드**로
타일넷에 붙습니다. 호스트의 테일스케일을 쓰지 않는 이유는 Funnel의 호스트명이
**노드당 하나**이기 때문입니다 — 호스트 노드를 쓰면 이 머신이 서비스하는 다른
것들과 주소를, 그리고 인증서를 공유하게 됩니다.

`TS_HOSTNAME`이 URL을 정하고, 이 값을 넣는 것이 사이드카를 켜는 스위치입니다.

```bash
TS_HOSTNAME=thsvkd-korail     # → https://thsvkd-korail.<테일넷>.ts.net/
```

노드 승인은 두 가지 중 하나입니다.

- **링크 클릭 (기본).** `TS_AUTHKEY`를 비워두면 `scripts/deploy.sh up`이 로그인
  링크를 출력하고 **바로 끝납니다.** 기다리는 것은 백그라운드의 로그인 헬퍼
  컨테이너이므로 급하지 않습니다. 링크를 연 뒤 같은 명령을 다시 실행하면
  이어서 올라갑니다. 몇 번을 실행해도 같은 링크가 나옵니다.
- **인증 키.** 시작을 지켜볼 수 없는 호스트라면
  [관리 콘솔](https://login.tailscale.com/admin/settings/keys)에서 키를 만들어
  `TS_AUTHKEY`에 넣습니다. **Reusable로 만들고 Ephemeral은 끄십시오** — 일회용
  노드는 재시작할 때마다 사라져 이름 뒤에 숫자가 붙고, 그러면 URL이 바뀝니다.

> 왜 로그인이 별도 컨테이너인가: 사이드카 이미지는 `tailscale up`을 60초 만에
> 죽이고 컨테이너를 재시작하는데, 재시작할 때마다 **노드 키가 새로 생겨 직전
> 링크가 무효**가 됩니다. 터미널을 보고 있지 않으면 이미 죽은 링크를 쫓게
> 됩니다. 헬퍼는 그 부트 스크립트를 우회해 무기한 기다리고, 승인되는 것은
> 상태 디렉터리이므로 사이드카가 그대로 물려받습니다.

### 이미지를 잊지 마십시오

`deploy.sh up`은 이미지를 다시 만들지 않습니다. 미니 앱 코드를 처음 배포한다면
`scripts/server.sh restart --build`(또는 `scripts/deploy.sh build`)로 먼저 이미지를
갱신하십시오. 낡은 이미지에는 공개 리스너가 없어서, Funnel은 붙었는데 **502**가
나옵니다.

`TS_HOSTNAME`이 비어 있으면 사이드카는 아예 뜨지 않습니다. compose 프로파일로
감싸 두었으므로 미니 앱을 쓰지 않는 설치는 이 서비스의 존재에 영향을 받지
않습니다.

### 3. 띄우기 — serve 와 funnel

```bash
scripts/deploy.sh up               # 타일넷 전용 (Serve)
scripts/deploy.sh --publish up     # 인터넷 공개 (Funnel).  -pb 도 같습니다
```

기본이 타일넷 전용인 것은 의도한 것입니다. 인증 키를 넣은 것은 "내 타일넷에
올리겠다"는 뜻이지 "인터넷에 열겠다"는 뜻이 아닙니다. 본인 폰이 타일넷에 들어와
있다면 이 상태로도 미니 앱을 다 써볼 수 있으므로, 공개는 다른 사용자가 필요할 때
하면 됩니다.

고른 값은 `.env`의 `TS_SERVE_MODE`에 기록됩니다. 한 번의 명령에만 적용되게 하면
다음 주에 다른 이유로 봇을 재시작했을 때 미니 앱이 조용히 인터넷에서 사라지고
로그에는 아무 말도 남지 않기 때문입니다. `scripts/server.sh restart`는 기록된
모드를 유지하고, 모드를 바꾸는 것은 `deploy.sh`뿐입니다.

`deploy.sh up`은 올라온 뒤 실제 주소를 출력합니다. 노드에게 물어서 얻는 값이라
추측이 아닙니다. 그 값을 `MINI_APP_URL`에 넣고 봇을 재시작하십시오.

`--publish`는 타일넷 정책에서 이 노드에 Funnel을 허용해야 동작합니다.

```json
"nodeAttrs": [{"target": ["tag:korail-bot"], "attr": ["funnel"]}]
```

> 참고로 `tailscale serve --service`로 만드는 Tailscale Services는 고유한 이름과
> VIP를 받지만 **타일넷 전용**이라 Funnel로 공개할 수 없습니다 —
> `tailscale funnel`에는 `--service` 플래그가 없습니다.

### 다른 방식을 쓰려면

사이드카를 쓰지 않고 앞단에 Cloudflare Tunnel 같은 것을 두어도 됩니다. compose가
공개 리스너를 호스트 루프백(`MINI_APP_BIND_ADDRESS`, 기본 `127.0.0.1`)에 매달아
두므로 거기로 프록시하면 됩니다.

```bash
cloudflared tunnel --url http://127.0.0.1:8081
```

**코드는 어느 쪽이든 동일합니다.** 바뀌는 것은 앞단과 `MINI_APP_URL` 값뿐입니다.

### 4. Telegram에 등록

봇을 재시작하면 `/start`의 답장 키보드와 채팅 입력창의 `예약 열기` 메뉴가 이
주소를 엽니다. 봇 프로필에도 **앱 열기** 버튼을 띄우려면 `@BotFather`에서
`/mybots` → 봇 → **Bot Settings** → **Configure Mini App** → **Enable Mini App**
을 고르고 같은 URL을 등록합니다.

어느 입구로 열든 화면은 동일하게 동작합니다. 예전에는 프로필·메뉴에서 연 화면이
`sendData()`를 쓸 수 없어 조건을 64자 `/start` 파라미터로 압축해 되돌렸는데,
이제는 어느 쪽이든 API로 직접 대화하므로 그 우회가 필요 없습니다. 그 경로를
처리하는 서버 코드(`ma1_`)는 예전 정적 페이지를 아직 가리키는 배포를 위해
남아 있습니다.

### 화면은 어디서 오는가

`webapp/`은 빌드 과정이 없고 Docker 이미지에 `/app/webapp`으로 들어갑니다
(`MINI_APP_WEBAPP_DIR`로 바꿀 수 있습니다). 페이지와 API가 같은 오리진이므로
CORS가 필요 없고, 철도 계정을 다루는 봇의 로그인 화면을 제3자 호스팅이 바꿔칠
수 있는 여지도 없습니다.

GitHub Pages 배포 워크플로는 **삭제했습니다.** 이 화면은 더 이상 정적 페이지가
아니라서, Pages에 올리면 API가 없는 오리진에서 모든 동작이 실패하는 화면이
됩니다. 열리기는 하는데 아무것도 되지 않는 페이지를 배포해 두는 것보다, 배포하지
않는 편이 정직합니다. `MINI_APP_URL`은 이제 항상 봇 자신을 가리킵니다.

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
