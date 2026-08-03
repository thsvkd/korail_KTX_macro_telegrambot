# AGENTS.md

이 저장소에서 일하는 에이전트를 위한 안내입니다. 사람이 읽어도 되지만,
대상은 "배포해줘" 같은 한마디를 받고 무엇을 해야 하는지 정해야 하는 쪽입니다.

## 무엇을 하는 프로젝트인가

코레일과 SR(SRT)의 취소표를 대신 지켜보다 자리가 나면 예약하는 텔레그램
봇입니다. 검색 하나가 별도 프로세스로 돌면서 몇 초에 한 번씩 조회하고,
좌석을 잡으면 사용자에게 결제를 안내합니다. **결제는 하지 않습니다** —
예약까지가 봇의 일이고 결제는 사람이 직접 합니다.

## 배포 — 먼저 읽으십시오

### 이 봇은 docker compose 스택으로 돕니다

운영 봇은 `korail_bot` + `korail_redis` 컨테이너입니다(테스트 봇은
`korail_bot_test` + `korail_redis_test`). 호스트에 `waitress-serve` 프로세스는
없는 것이 정상입니다.

```bash
./scripts/server.sh status         # 살아 있으면 종료코드 0
./scripts/server.sh restart        # 배포 = 이것 (앱 컨테이너만 새로 만든다)
./scripts/server.sh restart --build  # 코드가 바뀌었으면 이미지부터
./scripts/server.sh logs 50
```

`restart` 는 Redis 를 건드리지 않고 앱 컨테이너만 교체하며, 새 컨테이너가 실제로
기동했는지 확인한 뒤에 끝납니다.

### 데이터는 컨테이너 밖에 있습니다

Redis 상태는 이름 있는 볼륨이 아니라 `REDIS_DATA_DIR`(기본 `./.data/redis`)에
바인드 마운트됩니다. `stop`, `down`, 컨테이너 삭제, `docker volume prune` 어느
것도 등록 계정·세션·검색 상태를 지우지 않습니다. 지우려면 그 디렉터리를 지워야
하고, 그 경로는 `deploy.sh down --purge-data` 하나뿐입니다.

파일은 Redis 컨테이너의 uid 소유이므로 호스트에서 직접 읽고 쓰려 하지 말고
컨테이너를 거치십시오.

### --host 는 디버깅용입니다

`server.sh <명령> --host` 는 `.venv` 프로세스와 **별도 개발용 Redis** 를 씁니다.
배포된 스택의 데이터가 보이지 않으므로, "계정이 다 사라졌다" 로 오해하기 쉽습니다.
운영 작업에는 쓰지 마십시오.

### 재시작 전 확인

진행 중인 검색 프로세스를 내리게 됩니다. **몇 시간째 취소표를 기다리던
사용자가 있을 수 있습니다.**

```bash
./scripts/server.sh status      # "진행 중인 작업" 절을 보십시오
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

4.0.0~4.2.0 에는 태그가 없고 `chore(release):` 커밋으로만 표시돼 있습니다.
그 시절의 버전 이력을 찾을 때는:

```bash
git log --oneline --grep='chore(release)'
```

**v4.2.2 부터는 릴리스마다 태그를 달고 GitHub 릴리스를 만듭니다.** 태그는
`chore(release):` 커밋에 달고, 릴리스 본문은 `release_notes.py` 의 그
버전 엔트리를 그대로 씁니다 — 사용자가 채팅에서 받는 글과 저장소에 남는
글이 다를 이유가 없습니다.

```bash
git tag -a v4.2.2 -m 'v4.2.2' && git push origin master --follow-tags
gh release create v4.2.2 --title 'v4.2.2' --notes-file <(...)
```

4.0.0~4.2.0 에 소급해서 다는 것은 **아직 정해지지 않았습니다.** 그 버전들의
이력은 위의 `git log --grep` 으로 찾습니다.

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
