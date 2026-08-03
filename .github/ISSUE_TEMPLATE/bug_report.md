---
name: 버그 신고
about: 봇이 기대와 다르게 동작할 때
title: '[Bug] '
labels: bug
assignees: ''
---

> **먼저 읽어주세요**
>
> - **보안 취약점은 여기에 올리지 마십시오.**
>   [SECURITY.md](https://github.com/thsvkd/korail_KTX_macro_telegrambot/blob/master/SECURITY.md)
>   의 비공개 신고 경로를 이용해 주십시오.
> - **`.env` 파일 내용을 통째로 붙여넣지 마십시오.** 봇 토큰, `SESSION_SECRET`,
>   `REDIS_PASSWORD`, 코레일 비밀번호가 들어 있습니다.
> - **봇 토큰과 코레일 계정 정보(휴대전화번호, 비밀번호)는 절대 붙여넣지
>   마십시오.** 로그를 붙일 때는 전화번호와 chat_id 를 가려주십시오
>   (예: `010-****-5678`, `chat_id=****3210`).
> - 아래 안내 문단(`>` 로 시작하는 줄)은 작성 후 지워도 됩니다.

## 무슨 일이 일어났나요

<!-- 관찰한 동작을 한두 문장으로 -->

## 기대한 동작

<!-- 어떻게 동작했어야 하나요 -->

## 재현 절차

<!-- 대화 단계까지 적어주시면 좋습니다 -->

1.
2.
3.

## 실행 환경

- 실행 방식: <!-- compose 스택(scripts/server.sh start) / 로컬(--host) / 기타 -->
- 커밋 또는 이미지 태그: <!-- git rev-parse --short HEAD 결과 또는 이미지 태그 -->
- OS / 아키텍처: <!-- 예: Raspberry Pi OS aarch64, Ubuntu 24.04 x86_64 -->
- Python: <!-- uv run python -V (직접 실행한 경우만) -->
- Redis: <!-- compose 기본 / 외부 Redis / scripts/server.sh redis --host -->

## 관련 설정

<!--
값이 아니라 '설정했는지 여부'만 적어주십시오. 값은 필요 없습니다.
해당하는 것에 x 표시:
-->

- [ ] `SESSION_SECRET` 을 설정했다
- [ ] `RESUME_ON_RESTART` 가 켜져 있다 (기본 true)
- [ ] `USERID` / `USERPW` 를 설정했다
- [ ] `PREAPPROVED_USERS` 또는 이전 이름 `ALLOW_LIST` 를 설정했다
- [ ] `ADMIN_PASSWORD` 를 설정했다

## 로그 발췌

<!--
문제가 발생한 앞뒤 20~50줄 정도면 충분합니다.
붙이기 전에 전화번호, chat_id, 토큰류를 반드시 가려주십시오.

로그 위치:
  - compose 스택: scripts/server.sh logs 100
  - 포그라운드 실행(scripts/server.sh start --foreground): 터미널 출력
  - 로컬 데몬 실행(scripts/server.sh start --host): .run/korail-bot.log
-->

```
여기에 붙여넣기
```

## 그 밖에 참고할 내용

<!-- 재현 빈도, 최근에 바꾼 설정, 코레일 사이트 변경 여부 등 -->
