# 테스트 안내

테스트는 외부 Korail·Telegram API를 호출하지 않으면서 입력 검증부터 전체 예약
대화까지 확인합니다. 2026-08-01 기준 수집 결과는 총 **1,455개**입니다.

| 구분 | 개수 | Redis·Docker | 범위 |
| --- | ---: | --- | --- |
| `tests/unit` | 1,224 | 불필요 | 순수 로직, 라우팅, 키보드, 서비스의 격리 동작 |
| `tests/integration` | 224 | 필요 | 실제 Redis 저장·TTL과 여러 계층의 협력 |
| `tests/e2e` | 7 | 필요 | `/start`부터 검색 시작·취소까지 전체 대화 |

정확한 현재 개수는 다음 명령으로 다시 확인합니다. 매개변수화된 테스트는 하나의
함수에서 여러 사례로 수집되므로 함수 선언 수와 실제 테스트 수가 다릅니다.

```bash
uv run --frozen pytest --collect-only -q
```

## 실행 준비

```bash
uv sync --frozen
```

단위 테스트만 실행할 때는 Docker가 필요 없습니다. 통합·E2E 테스트는
testcontainers가 `redis:7-alpine`을 일회용으로 띄우므로 Docker 데몬이 실행 중이어야
합니다. 실제 봇 토큰과 코레일 계정은 필요하지 않습니다. `tests/conftest.py`가
임포트 전에 테스트용 설정값을 넣고 `USERID`·`USERPW` 같은 로컬 값을 제거합니다.

## 자주 쓰는 명령

```bash
# 전체
make test
./scripts/test.sh

# 단위 테스트만
make test-unit
./scripts/test.sh tests/unit -v

# 디렉터리·파일·테스트 하나
./scripts/test.sh tests/integration
./scripts/test.sh tests/unit/test_validators.py
./scripts/test.sh tests/unit/test_validators.py::TestTimeValidation::test_valid_midnight

# 이름으로 골라 실행
./scripts/test.sh -k payment

# 첫 실패에서 중단
./scripts/test.sh -x

# 커버리지
uv run --frozen pytest tests --cov=src/korail_bot --cov-report=term-missing
uv run --frozen pytest tests --cov=src/korail_bot --cov-report=html
```

`scripts/test.sh`는 넘겨받은 인자를 pytest에 그대로 전달하고, 테스트가 임포트할 때
필요한 기본 환경변수를 채웁니다. Docker가 없어 보이면 경고하지만 명령을 막지는
않으므로, 그 환경에서는 반드시 `tests/unit`으로 범위를 한정하십시오.

## 디렉터리 구성

### 단위 테스트

`tests/unit`은 Redis 연결 없이 실행됩니다.

- `test_admin_auth.py`, `test_operator_commands.py`, `test_operator_screens.py`:
  관리자 인증·잠금·운영 명령
- `test_callback_routing.py`, `test_input_handling.py`, `test_refused_answers.py`:
  Telegram 텍스트와 인라인 버튼 라우팅
- `test_command_menu.py`, `test_keyboard_privilege.py`, `test_keyboards.py`:
  일반·개발자 방 메뉴와 키보드 권한
- `test_credential_handling.py`, `test_preconfigured_login.py`,
  `test_security.py`: 자격증명 전달·암호화·개발자 계정 경계
- `test_favourite_model.py`: 즐겨찾기 모델과 직렬화
- `test_going_back.py`, `test_schedule_conversation.py`,
  `test_scheduled_search.py`, `test_train_selection.py`:
  예약 대화의 분기, 뒤로 가기, 열차 선택, 시작 시각 예약
- `test_korail_client.py`, `test_korail_search.py`,
  `test_search_failure_handling.py`, `test_search_pacing.py`:
  Korail 클라이언트 격리, 검색·재로그인·장애 백오프
- `test_search_process.py`, `test_search_process_shutdown.py`,
  `test_stopping_a_search.py`: 자식 프로세스 수명주기와 종료
- `test_restart_recovery.py`, `test_search_watchdog.py`:
  재시작 복구와 멈춘 검색 감시
- `test_payment_check_api.py`, `test_payment_reminders.py`,
  `test_payment_verification.py`, `test_multi_seat_reminders.py`:
  예약 뒤 결제 확인과 단일·다중 좌석 알림
- `test_progress_reports.py`, `test_release_announcement.py`:
  진행 보고 설정과 버전 업데이트 알림
- `test_station_names.py`, `test_train_type_guidance.py`, `test_validators.py`:
  역·날짜·시간·선택지 검증과 사용자 안내
- `test_telegram_api.py`, `test_telegram_poller.py`:
  Telegram API 요청, long polling, 중복 업데이트 처리
- `test_startup.py`: 시작 시 메뉴·서비스 기동과 종료 순서

### 통합 테스트

`tests/integration`은 일회용 Redis를 실제로 사용합니다.

- `test_access_control.py`, `test_developer_mode.py`:
  체험 횟수, 승인 요청, 개발자 방 저장
- `test_conversation_handler.py`, `test_onboarding.py`, `test_favourites.py`:
  대화 상태와 장기 보관 계정·즐겨찾기
- `test_reservation_service.py`, `test_reservation_callback.py`:
  검색 프로세스 레코드와 내부 콜백 정리
- `test_payment_reminder.py`:
  결제 상태 저장, 알림 중단, 타임아웃
- `test_shutdown_and_resume.py`, `test_dead_search.py`:
  정상 종료·재시작 복구와 watchdog 경로
- `test_progress_report_preference.py`:
  진행 알림 설정 저장과 만료
- `test_storage_scan.py`, `test_storage_scheduled_search.py`,
  `test_storage_ttl.py`: Redis SCAN, 예약 검색 직렬화, 키별 TTL
- `test_refactored_app.py`: 설정·스토리지·핸들러의 기본 결합

### E2E 테스트

`tests/e2e/test_full_reservation_flow.py`는 외부 API와 자식 프로세스만 가짜로 바꾸고
실제 대화 상태와 Redis를 통해 다음 일곱 여정을 검사합니다.

1. 1명 예약 정상 흐름
2. 여러 명 연속 좌석
3. 여러 명 랜덤 배치
4. 진행 중 취소
5. 로그인 실패 뒤 재시도
6. 시작 확인에서 거절
7. 최종 확인에서 거절

## fixture와 격리 방식

`tests/conftest.py`는 명령줄 경로가 모두 `tests/unit` 아래일 때 Redis 컨테이너를
띄우지 않습니다. 전체 실행, 인자 없는 실행, `-k`만 있는 실행처럼 Redis가 필요한지
확정할 수 없는 경우에는 보수적으로 컨테이너를 시작합니다.

통합·E2E 실행에서는 세션 범위 Redis 컨테이너 하나를 쓰고 각 테스트 fixture가
필요한 상태를 정리합니다. 애플리케이션 설정은 다음 원칙으로 격리됩니다.

- `BOTTOKEN`, `SESSION_SECRET`, `ADMIN_PASSWORD`는 테스트용 더미값을 사용합니다.
- 개발자 셸의 `USERID`, `USERPW`, `REDIS_PASSWORD`는 제거합니다.
- Redis 호스트와 포트는 testcontainers가 만든 값으로 덮습니다.
- Korail, Telegram, 백그라운드 프로세스 호출은 각 테스트 경계에서 mock 또는
  fake로 교체합니다.

설정 모듈은 임포트 시 환경변수를 읽습니다. 특정 설정을 검증하는 테스트는 모듈을
다시 불러오거나 monkeypatch가 적용되는 위치를 확인해야 합니다.

## 새 테스트를 추가할 때

- 계산·검증·라우팅처럼 저장소 없이 설명되는 동작은 `tests/unit`에 둡니다.
- Redis 직렬화, TTL, 여러 서비스의 협력이 핵심이면 `tests/integration`에 둡니다.
- 사용자가 밟는 전체 상태 전이가 핵심일 때만 `tests/e2e`에 추가합니다.
- 버그 수정은 먼저 실패를 재현하는 가장 작은 테스트를 만들고, 같은 문제가 다시
  생기지 않도록 관찰 가능한 결과를 검증합니다.
- 실제 전화번호, bot token, chat_id, 코레일 비밀번호를 fixture나 assertion
  메시지에 넣지 않습니다.

pytest 설정은 `pyproject.toml`에 있습니다. 경고는 기본적으로 오류이며,
`redis_container` marker는 실제 Redis가 필요한 테스트를 표시합니다. CI는 단위
테스트와 통합·E2E 테스트를 별도 단계로 실행하고 둘 다 실패를 차단합니다.

## 문제 해결

### Docker 권한 또는 연결 오류

```bash
docker ps
./scripts/test.sh tests/unit -q
```

단위 테스트는 통과하지만 전체 테스트가 Docker socket 오류로 멈추면 Docker 데몬과
현재 사용자의 권한을 확인합니다. Linux에서 docker 그룹에 사용자를 추가했다면 다시
로그인해야 합니다.

### 임포트 오류

```bash
uv sync --frozen
uv run --frozen python -c "import korail_bot; print(korail_bot.__file__)"
```

이 프로젝트는 src 레이아웃 패키지로 설치되므로 `PYTHONPATH`를 직접 설정할 필요가
없습니다.

### 테스트 수가 문서와 다를 때

```bash
uv run --frozen pytest tests/unit --collect-only -q
uv run --frozen pytest --collect-only -q
```

새 테스트가 정상 수집되는지 확인한 뒤 이 문서의 날짜와 분류별 개수를 함께
갱신하십시오.
