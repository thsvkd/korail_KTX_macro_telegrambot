# 기여 안내

이 문서는 README 에 없는 것만 담습니다. 프로젝트가 무엇을 하는지, 환경변수가
무엇인지, 디렉터리가 어떻게 나뉘는지는 [README.md](README.md) 를 보세요.
특히 코드를 읽기 전에 [프로젝트 구조](README.md#프로젝트-구조) 절을 먼저 보면
어디에 무엇이 있는지 파악이 빠릅니다.

## 개발 환경 세팅

직접 설치해야 하는 것은 **uv** 와 **Docker** 두 가지입니다.
Python 3.13 은 따로 받지 않아도 됩니다 — `pyproject.toml` 의
`requires-python = ">=3.13"` 을 보고 uv 가 알아서 인터프리터를 내려받습니다.

```bash
# uv 설치 (한 번만)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 클론
git clone https://github.com/thsvkd/korail_KTX_macro_telegrambot.git
cd korail_KTX_macro_telegrambot

# .env 생성 + 시크릿 발급 + 의존성 설치
./scripts/setup.sh        # == make setup

# pre-commit 훅 설치 (선택이지만 권장)
uv run pre-commit install
```

`setup.sh` 는 `.env.example` 을 `.env` 로 복사하고(권한 600), 비어 있는 시크릿을
`scripts/setup.sh secrets` 로 채우고, `uv sync` 로 `.venv` 를 만듭니다. 이미 `.env`
가 있으면 건드리지 않습니다.

### Docker 가 왜 필요한가

두 군데에서 필요합니다.

- **로컬 실행**: `scripts/server.sh start` 는 Redis 에 못 닿으면 그 자리에서
  종료합니다. 개발용 Redis 는 `./scripts/server.sh redis start` (== `make redis`)
  가 컨테이너로 띄웁니다.
- **테스트**: `tests/integration` 과 `tests/e2e` 는 `tests/conftest.py` 가
  testcontainers 로 일회용 Redis 컨테이너를 띄워서 돌립니다. Docker 데몬이 없으면
  이 두 스위트는 돌지 않습니다.

Linux 에서 `docker` 명령에 권한 오류가 나면 사용자를 `docker` 그룹에 넣고 다시
로그인해야 합니다(자세한 절차는 [DEPLOYMENT.md](DEPLOYMENT.md) 상단 참고).

봇을 실제로 띄워 보려면 텔레그램 봇 토큰(@BotFather 의 `/newbot`)과 코레일 회원
계정이 추가로 필요합니다. 다만 **테스트를 돌리는 데는 둘 다 필요 없습니다** —
`tests/conftest.py` 와 `scripts/test.sh` 가 `BOTTOKEN` 등 앱이 임포트 시점에
요구하는 값을 더미 기본값으로 채웁니다.

## 테스트 실행

```bash
# 전체 (1,455개, Docker 데몬 필요)
make test

# 단위 테스트만 (1,224개, Docker 불필요)
make test-unit

# 부분 실행 — scripts/test.sh 는 인자를 pytest 로 그대로 넘깁니다
./scripts/test.sh tests/unit/test_validators.py
./scripts/test.sh -k crypto
```

`make test` 는 `scripts/test.sh` 를 부르고, 이 스크립트는 Docker 가 없어 보이면
경고만 하고 그대로 진행합니다. 그러면 통합/e2e 단계에서 실패하니, Docker 를 띄울
수 없는 환경에서는 `make test-unit` 을 쓰세요.

일회용 Redis 는 테스트가 끝나면 자동으로 정리됩니다. `.env` 의 값은 테스트에
쓰이지 않습니다(uv 는 `.env` 를 읽지 않습니다) — 로컬 `REDIS_PASSWORD` 가 일회용
컨테이너와 충돌할 걱정은 하지 않아도 됩니다.

## 코드 스타일

포매터와 린터는 ruff 하나입니다. 설정은 `pyproject.toml` 의 `[tool.ruff]`
(line-length 100).

```bash
make format     # ruff format . + ruff check --fix .
make lint       # ruff format --check . + ruff check .   (CI 와 동일)
make typecheck  # mypy
```

`uv run pre-commit install` 을 해두면 커밋할 때 ruff 가 자동으로 돌고, `uv-lock`
훅이 `pyproject.toml` 만 고치고 `uv.lock` 을 안 갱신한 경우를 잡아줍니다.

### 언어 규칙

- **코드 주석과 docstring 은 영어**로 씁니다.
- **문서(`.md`)와 사용자에게 보이는 텔레그램 메시지는 한국어**로 씁니다.
- 커밋 메시지도 한국어입니다(아래 참고).

### mypy 는 아직 통과 조건이 아닙니다

솔직하게 적어둡니다. CI 의 Type check 단계는 `continue-on-error: true` 라서
mypy 가 실패해도 CI 는 초록색입니다. 현재 클린 체크아웃에서
`make typecheck` 를 돌리면 **12개 파일에서 56개 오류**가 나옵니다(대부분
`str | None` 이 `int` 파라미터로 들어가는 유형). 오류는
`handlers/conversation_handler.py`와 `api/reservation_callback.py`에 몰려
있고 나머지 31개 소스 파일은 이미 깨끗합니다(2026-08-01, 총 43개 파일 검사).

즉, `make typecheck` 에서 오류가 보인다고 해서 당신이 깬 것이 아닙니다. 대신
**당신이 건드린 파일에서 오류 수가 늘지 않았는지**는 확인해 주세요. 새로 만드는
코드는 타입을 붙여서 씁니다.

## 커밋 규약

한국어 Conventional Commits 를 씁니다. 형식은 `type(scope): 무엇을 왜 했는지`,
제목은 **평서형 한국어 한 문장**으로 씁니다. 본문에는 "왜 이렇게 했는지" 를
서술형으로 길게 적는 것이 이 레포의 관행입니다.

실제 커밋 예시 (`git log --oneline` 에서 그대로):

```
feat(schedule): 검색 시작 시각을 예약할 수 있게 한다
feat(search): 시간대에서 감시할 열차를 골라낼 수 있게 한다
fix(validator): 괄호가 들어간 역 이름을 허용한다
fix(search): 코레일에 못 닿는 것과 매진을 구분한다
perf(storage): 키 목록을 KEYS 대신 SCAN 으로 훑는다
ci: 배포 앞에 테스트/린트 관문을 두고 arm64 를 함께 빌드한다
docs: uv 기반 워크플로에 맞춰 문서를 고친다
```

- 실제로 쓰이는 type: `feat`, `fix`, `perf`, `refactor`, `test`, `docs`,
  `build`, `ci`, `chore`
- scope 는 대체로 모듈/영역 이름입니다: `search`, `schedule`, `payment`,
  `telegram`, `storage`, `korail`, `scripts`, `validator`, `login`, `docker`
- scope 가 애매하면 생략해도 됩니다(`test:`, `docs:` 처럼).
- 커밋 메시지 본문에 **실행 로그를 그대로 붙여넣지 마세요**. 과거에 chat_id 와
  전화번호가 커밋 메시지로 들어간 사례가 있고, 커밋 메시지는 파일과 달리
  나중에 지우려면 히스토리 재작성이 필요합니다.

## PR 절차

1. `master` 에서 브랜치를 땁니다. 브랜치 이름 규칙은 따로 없지만
   `feat/열차-선택` 처럼 커밋 type 을 접두사로 쓰면 읽기 쉽습니다.
2. 올리기 전에 로컬에서 아래를 통과시킵니다.

   ```bash
   make lint
   make test        # Docker 가 없다면 최소한 make test-unit
   make typecheck   # 오류 수가 늘지 않았는지만 확인
   ```

3. PR 을 열면 `.github/pull_request_template.md` 가 본문에 자동으로 채워집니다.
   **무엇을 왜 바꿨는지**, 어떻게 확인했는지를 적고 체크리스트를 채워주세요.
4. 문서에 영향이 있으면 README/DEPLOYMENT 도 같은 PR 에서 고칩니다. 템플릿의
   '문서 갱신' 절에 어떤 문서가 영향을 받는지 항목으로 정리되어 있습니다.

### CI 가 PR 에서 무엇을 하는가

`.github/workflows/cicd.yml` 의 `check` job 만 돌고, 이 job 은 시크릿을 하나도
쓰지 않습니다. 그래서 fork 에서 올린 PR 도 그대로 통과합니다. 순서는 이렇습니다.

| 단계 | 명령 | 실패하면 |
| --- | --- | --- |
| Lint | `ruff format --check .` / `ruff check .` | 막힘 |
| Unit tests | `pytest tests/unit -q` | 막힘 |
| Integration and e2e | `pytest tests/integration tests/e2e -q` | 막힘 |
| Type check | `mypy` | **안 막힘** (`continue-on-error: true`) |

배포를 담당하는 `cicd` job 에는 네 개의 가드가 걸려 있어 PR 에서는 아예 돌지
않습니다 — pull_request 이벤트가 아닐 것, `refs/heads/master` 일 것, 저장소가
`thsvkd/korail_KTX_macro_telegrambot` 일 것, `vars.IMAGE_NAME` 이 설정돼 있을 것.
fork PR 에는 시크릿이 주입되지 않고 `GITHUB_TOKEN` 도 읽기 전용입니다.

> fork 에서 Actions 를 켜고 fork 의 `master` 에 푸시해도 저장소 가드와
> `vars.IMAGE_NAME` 가드에 걸려 `cicd` job 은 **실패가 아니라 skip** 됩니다.
> 남의 서버로 SSH 하거나 남의 이미지 태그로 푸시하는 일이 일어나지 않습니다.
> 자기 인프라로 배포해 보고 싶다면 fork 에 `IMAGE_NAME`(과 선택적으로
> `DEPLOY_HOST`) 변수를 설정해야 하는데, 그러면 저장소 가드 때문에 워크플로의
> `github.repository` 조건도 자기 저장소로 고쳐야 합니다.

## 버그 제보

[Issues](https://github.com/thsvkd/korail_KTX_macro_telegrambot/issues) 에 올려주세요.
새 이슈를 열면 버그 신고와 기능 제안 템플릿 중에서 고를 수 있습니다.

- 재현 방법을 알고 고칠 수 있다면 바로 PR 을 올려도 됩니다. 본문에 증상과 재현
  절차를 적어주세요.
- 고칠 방법이 확실하지 않거나 설계 논의가 필요하면 이슈를 먼저 여는 편이
  낫습니다. 방향이 어긋난 채로 구현이 끝나버리는 일을 줄일 수 있습니다.

제보할 때 있으면 좋은 정보:

- 무엇을 했고 무엇을 기대했고 실제로 무엇이 일어났는지
- 실행 방식(로컬 `server.sh` / Docker)
- `make status` 결과, 그리고 관련 로그 몇 줄

로그를 붙일 때는 **전화번호, 텔레그램 chat_id, 코레일 계정 정보를 지우고**
올려주세요. 앱 로그에서 전화번호는 `utils/privacy.py` 가 마스킹하지만 chat_id 는
원문으로 남습니다.

**보안 취약점은 여기에 해당하지 않습니다.** 자격증명 노출이나 인증 우회처럼
공개 전에 고칠 시간이 필요한 문제는 PR 이나 코멘트가 아니라
[SECURITY.md](SECURITY.md) 의 비공개 신고 경로를 이용해 주세요.

## 하지 말아야 할 것

- `.env` 를 커밋하지 마세요. `.gitignore` 는 `.env` 만 정확히 일치로 막고 있어서
  `.env.local`, `.env.prod`, `.env.bak` 같은 파생 파일은 걸러지지 않습니다.
  이런 파일을 만들었다면 커밋 전에 `git status` 로 직접 확인하세요.
- 커밋 메시지나 PR 본문에 실제 자격증명·전화번호·chat_id 를 넣지 마세요.
- `pyproject.toml` 의 `license` 필드와 `LICENSE` 파일은 임의로 만들거나 바꾸지
  마세요. 이 저장소는 라이선스가 지정되지 않은 upstream
  (GeunSam2/korail_KTX_macro_telegrambot) 의 포크라 라이선스 상태가 아직 정리되지
  않았고, 저장소 소유자가 결정할 사안입니다.
