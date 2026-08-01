# 테스트 안내

테스트는 외부 코레일·SR·Telegram API를 직접 호출하지 않으면서 입력 검증부터
전체 예약 대화까지 확인합니다. 2026-08-01 기준 전체 실행 결과는
**1,604개 통과**입니다.

| 구분 | 개수 | Redis·Docker | 범위 |
| --- | ---: | --- | --- |
| `tests/unit` | 1,344 | 불필요 | 모델, 라우팅, 키보드, 철도 서비스와 셸 스크립트 |
| `tests/integration` | 253 | 필요 | 실제 Redis 저장, 내부 예약 콜백, 코레일/SRT 대화 협력 |
| `tests/e2e` | 7 | 필요 | `/start`부터 검색 시작·취소까지 전체 흐름 |

정확한 현재 개수는 다음 명령으로 확인합니다.

```bash
uv run --frozen pytest --collect-only -q
```

## 실행

```bash
# 전체
./scripts/test.sh

# 단위 테스트만 — Docker 불필요
./scripts/test.sh tests/unit -q

# 통합·E2E — Docker 필요
./scripts/test.sh tests/integration tests/e2e -q

# 특정 테스트
./scripts/test.sh tests/unit/test_srt_service.py -q
./scripts/test.sh -k operator -q
```

통합·E2E 테스트는 testcontainers가 `redis:7-alpine`을 일회용으로 실행합니다.
실제 봇 토큰이나 철도 계정은 필요하지 않습니다. `tests/conftest.py`가 임포트
전에 테스트 설정을 넣고 로컬 `USERID`·`USERPW`·`SRT_ID`·`SRT_PW`를 제거합니다.

## 주요 SRT 및 배포 테스트

- `tests/unit/test_srt_service.py`: SR 응답 분류, 조회·예약·취소, 결제 상태
- `tests/unit/test_operator.py`: 사업자 파싱, 하위 호환과 역 목록
- `tests/unit/test_operator_routing.py`: 검색 프로세스의 철도 서비스 라우팅
- `tests/integration/test_srt_conversation.py`: SRT 대화 전체 구간과 뒤로가기
- `tests/integration/test_operator_storage.py`: 옛 Redis 레코드의 코레일 호환
- `tests/unit/test_setup_script.py`: `.env.test`, 시크릿, Compose, 토큰·PID 및 상태 조회 격리

## 테스트 격리 원칙

- 단위 테스트는 Redis와 Docker에 접속하지 않습니다.
- 통합 테스트의 Redis는 실행마다 새 컨테이너입니다.
- HTTP 테스트는 Telegram 공개 webhook이 아니라 내부
  `/reservation-callback` 엔드포인트를 검증합니다.
- 철도 클라이언트는 mock/fake로 대체하므로 실제 예약을 만들지 않습니다.
- 셸 스크립트 테스트는 임시 환경 파일과 가짜 Docker 실행 파일을 사용합니다.
