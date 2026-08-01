# 대화 플로우

이 문서는 `handlers/conversation_handler.py`와 `handlers/update_processor.py`의 현재
동작을 기준으로 예약 대화의 상태 전이를 설명합니다. `UserProgress`의 이름은
"완료된 입력"을 가리키지만, 런타임에서는 그 다음 질문을 기다리는 상태로
사용됩니다. 상태 숫자는 Redis에 저장되므로 새 단계를 중간에 끼우지 않고 뒤에
추가합니다.

## 기본 예약 흐름

| 현재 상태 | 기다리는 입력 | 처리 메서드 | 성공 후 상태 |
| --- | --- | --- | --- |
| `STARTED` | 시작할지 여부 | `_handle_start_confirmation` | `START_ACCEPTED` |
| `START_ACCEPTED` | 코레일 휴대전화번호 | `_handle_phone_input` | `ID_INPUT_SUCCESS` |
| `ID_INPUT_SUCCESS` | 코레일 비밀번호 | `_handle_password_input` | `PW_INPUT_SUCCESS` |
| `PW_INPUT_SUCCESS` | 출발일 | `_handle_date_input` | `DATE_INPUT_SUCCESS` |
| `DATE_INPUT_SUCCESS` | 출발역 | `_handle_src_station_input` | `SRC_LOCATE_INPUT_SUCCESS` |
| `SRC_LOCATE_INPUT_SUCCESS` | 도착역 | `_handle_dst_station_input` | `DST_LOCATE_INPUT_SUCCESS` |
| `DST_LOCATE_INPUT_SUCCESS` | 검색할 출발 시각 | `_handle_dep_time_input` | `DEP_TIME_INPUT_SUCCESS` |
| `DEP_TIME_INPUT_SUCCESS` | 검색 종료 시각 | `_handle_max_dep_time_input` | `MAX_DEP_TIME_INPUT_SUCCESS` |
| `MAX_DEP_TIME_INPUT_SUCCESS` | 열차 종류 | `_handle_train_type_input` | `TRAIN_TYPE_INPUT_SUCCESS` |
| `TRAIN_TYPE_INPUT_SUCCESS` | 일반실·특실 우선순위 | `_handle_special_option_input` | `SPECIAL_INPUT_SUCCESS` |
| `SPECIAL_INPUT_SUCCESS` | 승객 수 | `_handle_passenger_count_input` | `PASSENGER_COUNT_INPUT_SUCCESS` 또는 `SEAT_STRATEGY_INPUT_SUCCESS` |
| `PASSENGER_COUNT_INPUT_SUCCESS` | 좌석 배치 | `_handle_seat_strategy_input` | `SEAT_STRATEGY_INPUT_SUCCESS` |
| `SEAT_STRATEGY_INPUT_SUCCESS` | 감시할 열차 | `_handle_train_selection_input` | `TRAIN_SELECT_INPUT_SUCCESS` |
| `TRAIN_SELECT_INPUT_SUCCESS` | 최종 동작 | `_handle_final_confirmation` | `FINDING_TICKET`, `SCHEDULE_INPUT_PENDING` 또는 취소 |
| `SCHEDULE_INPUT_PENDING` | 검색 시작 시각 | `_handle_schedule_input` | 예약 저장 후 대화 종료 |
| `FINDING_TICKET` | 검색 중 | `_handle_already_processing` | 상태 유지 |

`INIT`은 진행 중인 대화가 없는 초기값입니다.

## 단계별 입력과 분기

### 1. 시작과 로그인

- `/start`는 세션을 `STARTED`로 만들고 시작 확인 버튼을 보냅니다.
- 처음 쓰는 일반 사용자는 안내에 동의한 뒤 휴대전화번호와 비밀번호를 입력합니다.
  전화번호는 하이픈 유무를 모두 허용하고, 비밀번호는 코레일 로그인으로 검증합니다.
- 로그인이 성공하면 계정을 암호화해 별도 온보딩 레코드로 보관합니다. 다음
  `/start`부터는 저장된 계정으로 다시 로그인한 뒤 날짜 질문으로 바로 갑니다.
- 개발자 방에 `USERID`와 `USERPW`가 모두 설정돼 있으면 두 입력을 건너뛰고 해당
  계정으로 로그인합니다. 실패하면 일반 전화번호 입력으로 돌아갑니다.
- 저장 계정의 로그인이 실패하면 보관 값을 삭제하고 재등록을 안내합니다.

접근 승인 여부는 로그인 입력 단계가 아니라 실제 검색 프로세스를 시작하기 직전에
확인합니다. 체험 한도가 남아 있으면 검색이 실제로 기동된 뒤 한 번 차감합니다.

### 2. 날짜·구간·시간대

- 출발일은 `YYYYMMDD` 형식이며 오늘부터 최대 1년 안이어야 합니다. 버튼은
  오늘부터 9일치를 제공합니다. 실제 발매 가능 기간은 코레일 정책이 더 짧을 수
  있습니다.
- 역 이름은 `역` 접미사를 빼고 입력합니다. 알려진 코레일 역인지 검증하며 출발역과
  도착역은 같을 수 없습니다.
- 검색 시작·종료 시각은 `HHMM`입니다. 종료 시각의 `2400`은 제한 없음을 뜻합니다.
  종료 시각은 시작 시각보다 뒤여야 합니다.

### 3. 열차·좌석·인원

- 열차 종류 `1`은 KTX 계열, `2`는 코레일의 모든 열차입니다.
- 좌석 옵션은 일반실 우선, 일반실만, 특실 우선, 특실만 중 하나입니다.
- 승객 수는 1~9명입니다.
- 1명이면 좌석 배치를 `consecutive`로 정하고 배치 질문을 건너뜁니다.
- 2명 이상이면 연속 좌석(`consecutive`) 또는 랜덤 배치(`random`)를 고릅니다.

### 4. 감시할 열차

입력한 날짜·구간·시간대로 현재 열차 목록을 조회합니다. 최대 30개까지 보여주며,
각 행을 반복해서 눌러 여러 열차를 선택하거나 해제할 수 있습니다.

- `101 105` 또는 `101,105`처럼 열차번호를 직접 입력할 수도 있습니다.
- 선택을 완료하면 고른 번호만 감시합니다.
- `시간대 전체 감시` 또는 직접 입력 `전체`/`0`은 특정 열차로 좁히지 않습니다.
- 새로고침은 여석과 목록을 다시 읽되 아직 운행하는 열차의 선택은 보존합니다.
- 목록 조회에 실패했거나 결과가 없으면 대화를 막지 않고 시간대 전체 감시로
  최종 확인 화면에 진입합니다.

### 5. 최종 확인

요약에는 날짜, 구간, 시간대, 열차·좌석 조건, 인원, 좌석 배치와 감시 범위가
표시됩니다. 여기서 다음 중 하나를 고릅니다.

- **지금 검색 시작**: 접근 권한과 서버 동시 검색 상한을 확인하고 자식 프로세스를
  띄웁니다. 기동에 성공해야 `FINDING_TICKET`이 되고 체험 횟수가 차감됩니다.
- **시작 시각 예약**: `SCHEDULE_INPUT_PENDING`으로 이동합니다.
- **즐겨찾기에 저장**: 현재 답을 저장하되 최종 확인 질문은 그대로 유지합니다.
- **뒤로**: 열차 선택 화면으로 돌아갑니다.
- **취소**: 세션을 초기화합니다.

### 6. 검색 시작 시각 예약

버튼의 절대 시각 외에 다음 입력을 허용합니다.

- `0700`: 다음 07:00
- `0801 0700`: 올해 8월 1일 07:00
- `20260801 0700`: 2026년 8월 1일 07:00

시각은 현재보다 뒤, 열차 출발보다 앞이어야 하며
`SCHEDULE_MAX_AHEAD_SECONDS` 안이어야 합니다. 예약이 저장되면 현재 대화는
종료되고 실제 검색은 스케줄러가 시작합니다. 앱이 예정 시각에 잠시 꺼져 있었으면
`SCHEDULE_GRACE_SECONDS` 안에서만 뒤늦게 시작합니다.

## 보조 흐름

### 온보딩과 계정 교체

- `/onboarding`과 별칭 `/init`은 코레일 계정 등록을 시작합니다.
- 이미 등록된 계정이 있으면 `ONBOARDING_OVERWRITE_PENDING`에서 교체 여부를 먼저
  묻습니다. 교체를 승인하면 기존 등록을 삭제한 뒤 새 전화번호를 받습니다.
- `/logout`, 봇 차단·대화방 삭제(`my_chat_member`),
  `CREDENTIAL_TTL_SECONDS` 만료 시 등록 계정이 삭제됩니다.

### 개발자 모드와 관리자 명령

- `ADMIN_MAGIC_STRING`은 명령과 대화 상태보다 먼저 검사합니다. 일치하면 그 방을
  개발자 방으로 만들고 기존 개발자 방에 전환 사실을 알립니다.
- 개발자 방은 체험 제한을 받지 않고 관리자 명령을 비밀번호 없이 실행하며,
  `USERID`/`USERPW`가 있으면 그 계정을 사용합니다.
- `/devoff`로 해제합니다. `ADMIN_MAGIC_STRING`이 비어 있으면 이 흐름은 없습니다.

### 사용 승인

- 체험 횟수를 모두 쓴 사용자는 최종 시작 시 승인 요청 버튼을 받습니다.
- 요청은 코레일 전화번호의 해시로 식별하므로 Telegram 방을 바꿔도 체험 횟수가
  초기화되지 않습니다.
- 운영자는 `/approve`로 승인·거절하고 `/users`로 승인 취소를 처리합니다.
- 서버 전체 검색 수가 `MAX_CONCURRENT_SEARCHES`에 도달하면 승인 여부와 무관하게
  새 검색을 시작하지 않습니다.

### 뒤로 가기와 오래된 버튼

- 비밀번호 질문을 제외하면 `◀️ 뒤로` 버튼과 직접 입력 `뒤로`가 모두 동작합니다.
  비밀번호 단계에서는 입력 문자열을 명령으로 오인하지 않도록 버튼만 받습니다.
- 1명 예약에서 열차 선택 화면 뒤로 가면 생략했던 좌석 배치 질문도 건너뜁니다.
- `update_processor.py`는 버튼에 담긴 단계와 현재 상태를 대조합니다. 이미 지나간
  질문이나 아직 오지 않은 질문의 버튼은 대화 입력으로 전달하지 않습니다.
- 취소 버튼은 어느 단계에서든 `/cancel`과 같은 경로로 처리됩니다.

## 상태 번호 호환성

`TRAIN_SELECT_INPUT_SUCCESS`, `SCHEDULE_INPUT_PENDING`,
`ONBOARDING_OVERWRITE_PENDING`의 숫자가 대화 순서와 달리 뒤쪽에 붙은 것은 의도된
동작입니다. 진행 상태가 Redis에 정수로 저장되므로 기존 숫자를 재배치하면 배포
사이에 살아남은 세션이 전혀 다른 질문으로 이동합니다. 새 상태를 추가할 때도 기존
값은 유지해야 합니다.
