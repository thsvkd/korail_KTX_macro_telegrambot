# SRT 지원 — PoC와 구현 핸드오프

**질문:** 지금 코레일에 하고 있는 취소표 감시를 SRT에도 할 수 있는가?

**답:** 할 수 있다. 조회·폴링은 실서버로 검증했고, 봇 전체를 두 사업자로
확장해 구현까지 마쳤다. **로그인·예약·취소는 아직 실계정으로 검증하지
않았다** — 그 부분이 이 문서에서 가장 중요한 미완이다.

조사·구현일: 2026-08-01
브랜치: `worktree-srt-support`

---

## 1. PoC 결과 (실측)

`SRTrain` 2.6.7 (`import SRT`, [ryanking13/SRT][srt])을 라즈베리파이에서 직접
호출해 얻은 값이다.

[srt]: https://github.com/ryanking13/SRT

| 확인한 것 | 결과 |
|---|---|
| 로그인 없이 열차 조회 | **된다.** 코레일과 다른 점 |
| 매진 열차 좌석 상태 노출 | `available_only=False` 로 실려온다 |
| 1초 간격 40회 폴링 | 40/40 성공, 차단·오류 0 |
| 응답 시간 | 중앙값 0.28s (min 0.24 / max 0.36) |
| netfunnel 대기열 | 키 1회 발급 후 계속 재사용, 대기 걸린 적 없음 |
| 미로그인 상태 예약 계열 호출 | 전부 `SRTNotLoggedInError` |
| 잘못된 자격증명 로그인 | `SRTLoginError: 존재하지않는 회원입니다` |

봇 기본 검색 간격(`SEARCH_INTERVAL=1`)과 그대로 맞는다.

### 코레일과 다른 점, 그리고 그것이 코드에 남긴 자국

1. **조회에 로그인이 필요 없다.** 그래도 봇은 로그인한다 — 예약에는 필요하고,
   세션이 검색 도중 상해 있으면 좌석이 나온 바로 그 순간이 아니라 그 전에
   알아야 하기 때문이다.
2. **매진 열차가 같은 응답에 실려온다.** korail2 는 서버가 걸러주지만 SR 은
   전부 주고 각각 예약 가능 여부를 말한다. 그래서 `available_only` 필터를
   `SrtService.search_trains` 가 직접 건다.
3. **일반실/특실을 따로 보고한다.** SR 이 말하는 "예약가능"은 둘 중 하나라도
   자리가 있다는 뜻이라, "일반실만" 검색이 특실만 남은 열차에서 멈추면 안
   된다. 그래서 좌석 등급이 *검색* 시점부터 필요하고, `SrtService(seat_type=)`
   로 생성자에서 받는다.
4. **`login()` 이 False 를 돌려주지 않고 예외를 던진다.** 그중 하나가
   `Your IP Address Blocked due to abnormal access.` 인데, 이건 폴링이 스스로
   불러온 결과이고 **재시도가 정확히 최악의 대응**이다. 그래서 `SrtBlockedError`
   로 다시 던져 루프 밖으로 내보낸다.
5. **`SRTDuplicateError` 는 선언만 되어 있고 어디서도 raise 되지 않는다.**
   (korail2 의 `KORAIL_PAYMENT` 상수와 같은 상황.) 중복 예약도 매진도 전부
   `SRTResponseError` 로 오고, 문장을 읽어서 구분해야 한다.
6. **결제 여부를 필드로 알려준다.** 코레일은 "목록에서 사라지면 결제된 것"
   이지만 SR 은 결제된 예약도 목록에 남기고 `paid` 로 말한다. 코레일보다
   정확하다.
7. **출발 시각 컷오프의 의미가 다르다.** 봇은 HHMM 으로 "이 시각 **전에**
   출발", SR 은 HHMMSS 로 "이 시각 **이하**". 1200 을 그대로 넘기면 12:00 열차가
   딸려 들어온다. `_time_limit()` 이 `115959` 로 바꾼다. 실서버로 확인:
   컷오프 1000 → 09:20 이 마지막.

### 예약대기(예약 대기열) 제도 — 이번 범위에 넣지 않았다

SR 에는 [예약대기 제도][standby]가 있다. 매진 열차의 취소표를 신청 순번대로
자동 배정하는 공식 기능이고, 라이브러리에도 `reserve_standby()` 가 있다.
매크로보다 나은 선택이지만 **조건이 있다.**

- 매진된 시점부터 신청 가능
- **출발 3일 전까지만 신청**, 2일 전 마지막 배정
- 열차당 전체 좌석의 10%까지

즉 **매크로가 필요한 구간은 정확히 "출발 3일 이내"** 이고, 그 바깥에서는
예약대기가 더 낫다. PoC 에서 조회한 D+1 열차들이 전부 "예약대기 불가능"으로
나온 것이 이 조건과 일치한다.

이번 구현은 폴링만 한다. 예약대기를 붙인다면 `SrtService.reserve_train` 옆에
`reserve_standby` 를 두고, 대화 흐름에서 출발일까지 남은 날짜를 보고 어느
쪽을 쓸지 정하는 모양이 될 것이다.

[standby]: https://www.srail.or.kr/cms/article/view.do?postNo=321&pageId=KR0502000000

---

## 2. 구현 구조

### 새로 생긴 이음매

```
services/rail_service.py     RailService (ABC) — 검색 루프, 지터, 실패 추적,
                             진행 보고, 재로그인 스케줄. 사업자를 모른다.
  ├── korail_service.py      KorailService — 코레일 고유 부분만 남았다
  └── srt_service.py         SrtService — SR 고유 부분

models/operator.py           Operator (korail | srt) + 역 목록 + 별칭
```

`KorailService` 는 954줄에서 절반 이하로 줄었고, 루프는 한 벌만 남았다.
기존 `korail_service` 에서 import 하던 `SearchProgress`,
`DuplicateReservationError`, `SearchUnavailableError` 는 그대로 re-export 한다.

서브클래스가 채우는 것은 여덟 개다: `login`, `_relogin`, `search_trains`,
`reserve_train`, `is_reservation_outstanding`, `reservation_id`,
`payment_due`, `describe_train`. 뒤의 셋은 **두 클라이언트가 같은 사실을 다른
이름으로 들고 있기 때문에** 생겼다 —
`rsv_id`↔`reservation_number`, `buy_limit_date`↔`payment_date`,
`train_no`↔`train_number`. 예약에 성공한 뒤의 코드는 전부 이 셋만 거친다.

### 사업자가 흐르는 경로

```
대화(사업자 선택) → session.train_info["operator"]
                  → TrainSearchParams.operator
                  → argv[12]  (백프로세스)
                  → SrtService / KorailService
```

argv 는 **맨 뒤에** 붙였다. 앞자리들의 의미가 그대로라, 이 배포 전에 시작된
검색이 재개될 때도 읽히는 자리가 어긋나지 않는다.

### 하위호환 — 이 변경에서 가장 조심한 부분

Redis 에 있는 모든 기록에는 사업자가 없다. **그것들은 전부 코레일 검색이다.**
그 판단을 `Operator.parse()` 한 곳에서 하고, 읽는 열두 군데가 각자 의견을 갖지
않게 했다.

| 저장소 | 없을 때 |
|---|---|
| `TrainSearchParams.operator` | korail |
| `FavouriteSearch.operator` | korail |
| `OnboardedAccount.operator` | korail |
| argv[12] 없음 | korail |
| 알 수 없는 값 | korail (검색을 못 읽는 것보다 낫다) |

계정 키도 코레일 것은 **그대로 두었다.**

```
user_credentials:{chat}         코레일 (예전부터 이 자리)
user_credentials:srt:{chat}     SRT (새로 추가)
```

SRT 쪽 키에서 `srt` 를 chat_id **앞**에 둔 이유: 방송 대상 목록이
`user_credentials:*` 를 스캔해 마지막 조각을 chat_id 로 읽기 때문이다.

단, `Operator.parse()` 와 달리 **사용자의 답**을 읽을 때는 모르는 값을 코레일로
넘기지 않는다(`Operator.from_answer` → None → 다시 묻는다). 저장된 기록을 잘못
읽으면 진행 중인 검색이 어긋나는 정도지만, 방금 한 질문의 답을 잘못 읽으면
**말없이 다른 철도를 예약한다.**

### 대화 흐름 변화

- `UserProgress.OPERATOR_INPUT_PENDING = 18` — 대화에서는 첫 질문이지만 번호는
  마지막이다. 이 숫자가 Redis 에 있어서, 재번호를 매기면 배포를 넘긴 세션이
  자기가 답하던 것과 다른 질문으로 옮겨간다.
- 역 키보드·역 검증이 사업자별로 갈린다. SR 은 33개 역이 고정이라 확정적으로
  판단하고, 코레일은 기존 동적 목록에 맡긴다(`Operator.serves()` 가 코레일에는
  `None` 을 준다 — "여기서 답하지 않는다"는 뜻이고 `False` 와 다르다).
- SRT 는 열차 종류 질문을 건너뛴다. 인원이 1명일 때 좌석 배치를 건너뛰는 것과
  같은 방식이고, **뒤로가기도 같이 건너뛴다.**
- 좌석 옵션 네 가지는 두 라이브러리가 이름을 똑같이 쓴다
  (`GENERAL_FIRST`/`GENERAL_ONLY`/`SPECIAL_FIRST`/`SPECIAL_ONLY`). 그래서 이름만
  뽑아 각자의 enum 에서 찾는다 — 코레일로 저장한 즐겨찾기가 SRT 에서도 뜻을
  갖는다. (`ReserveOption` 은 Enum 이 아니라 평범한 클래스라 `getattr` 로 찾는다.)

---

## 3. 검증된 것과 안 된 것

**테스트 1604개 통과** (유닛 1344 + 통합/E2E 260). ruff 는 통과했고,
mypy 51개는 이 변경 전부터 있던 기존 오류다.

새로 쓴 테스트:

| 파일 | 개수 | 무엇을 |
|---|---|---|
| `tests/unit/test_srt_service.py` | 48 | 컷오프 경계, 좌석 등급 필터, 거절 문장 분류, IP 차단, 결제 판정, 취소 |
| `tests/unit/test_operator.py` | 31 | 사업자 해석, 하위호환, 역 목록 |
| `tests/unit/test_operator_routing.py` | 24 | argv → 클라이언트 라우팅, 예약 객체 읽기 |
| `tests/unit/test_setup_script.py` | 12 | 테스트 봇 토큰·시크릿·프로세스·Compose 배포·Redis 자동 기동 및 교차-worktree 상태 격리 |
| `tests/integration/test_operator_storage.py` | 7 | 옛 형식 레코드가 코레일로 읽히는지 |
| `tests/integration/test_srt_conversation.py` | 20 | SRT 대화 전 구간, 역 거절, 질문 건너뛰기, 뒤로가기 |

**실서버로 확인한 것:** 조회, 폴링 안정성, 컷오프 경계, netfunnel 재사용.

**아직 실계정으로 확인하지 못한 것 — 다음에 할 일:**

1. `login()` 성공 경로. 실패 경로(잘못된 비번)만 봤다.
2. `reserve()` 성공. 좌석이 나왔을 때 실제로 잡히는지.
3. **중복 예약·매진 시 SR 이 실제로 뭐라고 하는지.** 지금
   `DUPLICATE_MARKERS` / `SOLD_OUT_MARKERS` 의 문구는 **추정이다.** 못 알아본
   거절은 재시도 가능한 쪽으로 처리하고 원문을 warning 으로 남기게 해 뒀으니,
   실계정 검증 때 로그에서 실제 문장을 주워 목록을 채우면 된다.
   (`grep "Unrecognised SR refusal"`)
4. 세션 만료 시 SR 응답. `SESSION_EXPIRED_MARKERS` 도 같은 성격의 추정이다.
5. `cancel()` 실동작.
6. 결제 기한 필드(`payment_date`/`payment_time`)의 실제 형식.

검증 스크립트는 scratchpad 에 있다(`poc_04_reserve.py` — 예약 1건 만들고 즉시
취소, `finally` 로 3회 재시도, 결제는 부르지 않음). 미결제 예약은 취소가
실패해도 10분 뒤 SR 이 회수하고, 결제 전 취소라 위약금이 없다.

---

## 4. 이어받기에서 완료한 것

- **`/fav` 철도 표시.** 목록 버튼에 `[코레일]`/`[SRT]` 배지를 붙이고 상세
  화면에도 철도를 표시한다. 같은 이름·구간의 즐겨찾기도 어느 계정을 쓰는지
  고르기 전에 알 수 있다.
- **SRT 사전설정 로그인.** `SRT_ID`/`SRT_PW` 를 추가했다. 코레일의
  `USERID`/`USERPW` 와 독립적으로 읽고, 개발자 방에서 SRT 를 선택했을 때만
  쓴다. `/onboarding` 도 철도를 고른 뒤 그 철도에 고정 계정이 있는지를
  판단하므로, 코레일 고정 계정이 SRT 계정 등록을 막지 않는다.
- **철도별 동시 검색 한도.** `MAX_CONCURRENT_SEARCHES` 값은 그대로지만
  코레일과 SRT 를 따로 센다. 예를 들어 기본값 5에서는 코레일 검색 5개가
  돌아가는 중에도 SRT 검색을 시작할 수 있다.

## 5. 배포 전 테스트 봇

`scripts/setup.sh --test`가 운영과 격리된 `.env.test`를 만든다. 별도
Telegram 토큰, Compose 프로젝트, 앱·Redis 컨테이너와 Redis 볼륨을 사용한다.
Compose 앱 포트는 long polling에 필요하지 않아 호스트에 공개하지 않으며,
안전한 기본값으로 체험 검색 0회·철도별 동시 검색 1개·재시작 자동 재개 끔을
설정한다.

```bash
scripts/setup.sh --test
scripts/deploy.sh --test build
scripts/deploy.sh --test up
scripts/deploy.sh --test logs
```

Compose 대신 호스트에서 띄울 때는 아래 명령을 쓴다. `run.sh --test`는 운영
프로세스와 다른 PID 파일·로그·HTTP 8081·Redis 6380을 사용하므로 둘을 동시에
실행하거나 테스트 봇만 중지할 수 있다.

```bash
scripts/run.sh --test --daemon
scripts/status.sh --test
scripts/run.sh --test --stop
```

테스트 전용 Redis가 없으면 `run.sh --test`가 6380 포트에 자동으로 기동한다.
`run.sh --test redis`는 Redis만 따로 관리해야 할 때 쓴다.

설정 때 나온 개발자 문구를 테스트 봇에 보내면 SRT 고정 계정
(`SRT_ID`/`SRT_PW`)으로 로그인 성공·예약·취소를 운영 데이터와 섞지 않고
검증할 수 있다. 단, 철도사 서버와 예약은 실제이므로 검증 뒤 예약 목록을
직접 확인해야 한다.

## 6. 계속 남은 것

- **예약대기(`reserve_standby`)** — 위 1절 참조. 출발 3일 이전 구간에서는
  폴링보다 이쪽이 맞다. 다만 사용자가 "자동 예약"과 "공식 예약대기" 중
  무엇을 요청하는지 대화·취소·알림 의미까지 정해야 하므로 별도 기능으로 둔다.
- **`korail_id` / `korail_pw` 필드명.** 이제 SR 계정도 담는다. 저장 포맷과
  호출부 전체에 퍼져 있어, Redis 하위호환을 포함한 별도 마이그레이션으로
  진행하는 편이 안전하다.
- **SRT 실계정 검증.** 3절의 로그인 성공, 실제 예약·취소, 서버 거절 문구와
  결제 기한 필드 확인은 유효한 SRT 계정과 실제 예약 행위가 필요하다.
