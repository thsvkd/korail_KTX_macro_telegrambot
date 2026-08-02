# AGENTS.md

이 저장소에서 일하는 에이전트를 위한 안내입니다. 사람이 읽어도 되지만,
대상은 "배포해줘" 같은 한마디를 받고 무엇을 해야 하는지 정해야 하는 쪽입니다.

## 무엇을 하는 프로젝트인가

코레일과 SR(SRT)의 취소표를 대신 지켜보다 자리가 나면 예약하는 텔레그램
봇입니다. 검색 하나가 별도 프로세스로 돌면서 몇 초에 한 번씩 조회하고,
좌석을 잡으면 사용자에게 결제를 안내합니다. **결제는 하지 않습니다** —
예약까지가 봇의 일이고 결제는 사람이 직접 합니다.

## 배포 — 먼저 읽으십시오

### 이 봇은 Docker 로 돌고 있지 않습니다

**프로덕션은 호스트에서 직접 실행됩니다.** `docker ps` 에는 개발·테스트용
Redis 컨테이너만 보이는데, 그것을 보고 "프로덕션이 안 돌고 있다"고 판단하면
틀립니다.

```bash
./scripts/server.sh status      # 살아 있으면 종료코드 0
./scripts/server.sh restart     # 배포 = 이것
./scripts/server.sh logs 50     # 또는 .run/korail-bot.log
```

`restart` 는 새 프로세스가 뜰 수 있음을 확인한 뒤에야 기존 것을 내립니다.

### deploy.sh 를 쓰지 마십시오

`scripts/deploy.sh` 는 **Docker Compose 전용**이며 현재 운영과 별개
경로입니다(`scripts/README.md:10-11`). 지금 `deploy.sh up` 을 실행하면:

- Compose 가 자체 Redis 볼륨을 쓰므로 **등록된 계정과 세션이 없는 빈 상태**로
  뜹니다
- 같은 봇 토큰으로 두 개가 long polling 하며 업데이트를 서로 뺏습니다

Docker 로 옮기는 것은 데이터 이관이 따르는 별도 결정입니다. 지시 없이
진행하지 마십시오.

### 재시작 전 확인

진행 중인 검색 프로세스를 내리게 됩니다. **몇 시간째 취소표를 기다리던
사용자가 있을 수 있습니다.**

```bash
ps aux | grep telebotBackProcess | grep -v grep
```

0개가 아니면 사용자에게 알리고 판단을 받으십시오. 재시작 시 Redis 기록에서
검색을 복구하도록 되어 있지만(`Resume on restart: enabled`), 복구가 실패하면
사용자가 처음부터 다시 걸어야 합니다.

## 릴리스

`src/korail_bot/release_notes.py` 상단 docstring 이 규칙의 출처입니다. 세
가지를 **한 커밋**에 담습니다.

1. `pyproject.toml` 의 `version`
2. `make lock` (uv.lock 이 따라가게)
3. `release_notes.py` 의 `NOTES` 엔트리

릴리스 노트는 **표를 예매하는 사람의 언어**로 씁니다. 모듈 이름도, 리팩터링
얘기도, 채팅에서 볼 수 없는 것도 넣지 않습니다. `headline` 은 채팅에 그대로
뜨는 짧은 몇 줄이고, `detail` 은 접힌 채로 따라갑니다 — 작은 릴리스는
`detail` 없이 냅니다.

커밋 메시지는 `chore(release): v4.2.1` 형태입니다.

봇은 기동할 때 버전이 올라간 것을 감지해 사용자에게 알립니다
(`release_announcer`). 즉 **재시작하면 릴리스 노트가 발송됩니다.**

### 태그

`v4.2.1` 이 이 저장소의 **유일한 태그**입니다. 4.0.0~4.2.0 에는 태그가 없고
`chore(release):` 커밋으로만 표시돼 있습니다. 버전 이력을 찾을 때는:

```bash
git log --oneline --grep='chore(release)'
```

앞으로 태그를 계속 달지, 과거에 소급할지는 **아직 정해지지 않았습니다.**
임의로 정하지 말고 물어보십시오.

## 검증

완료를 보고하기 전에 돌립니다.

```bash
make test          # 전체 (1600여 개, 20여 초)
make test-unit     # 유닛만, Docker 불필요
ruff check src tests
ruff format --check src tests
```

통합·E2E 는 testcontainers 가 Redis 를 띄우므로 Docker 가 필요합니다.

## 이 저장소의 관례

- **커밋 메시지는 한국어 현재형 평서문**입니다: `fix(scope): ~한다`.
  무엇을 고쳤는지가 아니라 무엇이 달라지는지를 씁니다.
- **`docs/` 아래 새로 만든 md 는 커밋하지 않습니다.** 조사·설계 부산물은
  로컬 검토용입니다. 기존 md 를 고치는 것은 커밋해도 됩니다.
- **`TODO.md` 는 추적되지 않습니다.** 항목을 체크하거나 추가하되 커밋에는
  넣지 않습니다.
- 기본 브랜치는 `master` 입니다. `origin` 은 fork(`thsvkd`),
  `upstream` 은 원본(`GeunSam2`) 입니다.

## 코드에서 알아둘 것

- `RailService`(`services/rail_service.py`)가 검색·재시도·백오프 루프를 갖고,
  `KorailService` 와 `SrtService` 가 운영사별 차이만 채웁니다. 두 운영사의
  클라이언트는 필드 이름이 다르므로 `describe_train`, `reservation_id`,
  `payment_due` 같은 정적 메서드가 그 차이를 흡수합니다.
- **"자리가 없다"와 "물어보지 못했다"는 다릅니다.** 전자는 빈 리스트,
  후자는 `SearchUnavailableError` 입니다. 이것을 뭉개면 죽은 검색이 멀쩡히
  도는 것처럼 보입니다. 루프에서 새 예외를 던질 일이 있으면 이 구분을
  지키십시오.
- 검색은 `telebotBackProcess.py` 가 별도 프로세스로 돌립니다. 자격증명은
  argv 가 아니라 stdin 으로 넘어갑니다(호스트의 다른 프로세스가 argv 를
  읽을 수 있기 때문).
- `LOG_LEVEL=INFO` 이므로 정상 검색 중에는 1000회마다 찍히는
  `📊 Search attempt #N` 말고는 거의 아무것도 남지 않습니다. 조용한 로그가
  고장을 뜻하지 않습니다.

## 진행 중인 일

`.omc/handoffs/` 에 인계 문서가 있으면 먼저 읽으십시오. 지금은
`standby-reservation.md`(예약대기 지원)가 있고, 설계까지만 끝나 있습니다.
