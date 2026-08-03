"""
What each release changed, in the words the users get to read.

The bot announces itself after an update, and an announcement that only names
a number is noise: nobody acts on "v4.0.0 이 되었습니다". These are the lines
that make it worth the interruption, so they are written for the person who
books tickets rather than for whoever wrote the diff - no module names, no
refactors, nothing they cannot see from the chat.

Two parts, because the interruption should stay small. The headline is what
lands in the chat: a few lines someone reads without deciding to. The detail
arrives collapsed behind a fold - there for whoever wants it, costing nothing
to whoever does not. A release with four features and a paragraph each would
otherwise be a wall of text arriving unasked.

Releasing means bumping the version in pyproject.toml, running `make lock` so
the lockfile follows it, and adding the entry here - all in the same commit.
The key is the version as packaging normalises it, which is what the lookup
arrives with: 4.2.0b1, not 4.2.0-beta.1. A version with no entry is still
announced, plainly; that is the fallback, not the intent.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReleaseNote:
    """One release, as its users hear about it."""

    #: The few lines shown outright. Short on purpose - this is the part that
    #: interrupts someone, so it has to earn the interruption on its own.
    headline: str
    #: Everything else, shown folded. Optional: a small release has nothing to
    #: hide and should not grow a fold to prove it.
    detail: str = ""


NOTES: dict[str, ReleaseNote] = {
    "4.0.0": ReleaseNote(
        headline="""• ◀️ 뒤로 버튼으로 앞 단계로 돌아갈 수 있습니다.
• /fav 로 자주 타는 구간을 저장해두고 불러옵니다.
• /notify 로 검색 진행 상황을 받아볼 수 있습니다. (기본 꺼짐)""",
        detail="""◀️ 뒤로
날짜를 하루 잘못 골랐다고 처음부터 다시 할 필요가 없습니다. 뒤에 질문이
있는 모든 단계에 버튼이 있고, "뒤로" 라고 입력해도 됩니다.

⭐ 즐겨찾기 (/fav)
확인 화면의 "즐겨찾기에 저장" 을 누르면 날짜를 뺀 모든 답을 저장합니다.
다음부터는 /fav 에서 불러와 날짜만 고르면 됩니다. 이름 변경과 삭제도
/fav 안에서 합니다.

🔔 진행 상황 알림 (/notify)
검색이 도는 동안 정해둔 간격마다 얼마나 오래 몇 번 조회했는지, 코레일이
응답하고 있는지 알려줍니다. 기본은 꺼져 있습니다.

🚄 "모든 열차" 안내
무엇이 포함되는지, 그리고 먼저 나오는 자리를 잡기 때문에 무궁화호가
예약될 수 있다는 점을 고르기 전에 알려줍니다.""",
    ),
    "4.0.1": ReleaseNote(
        headline="""• /notify 에서 원하는 간격을 직접 입력할 수 있습니다.
• 메뉴와 /help 가 방에 따라 쓸 수 있는 명령어만 보여줍니다.""",
        detail="""⌨️ /notify 직접 입력
버튼에 없는 간격(예: 7분)도 "직접 입력" 을 눌러 정할 수 있습니다.

📋 명령어 메뉴와 /help
운영자 방에는 관리자 명령어까지 나오고, 그 외의 방에는 예약에 필요한
명령어만 나옵니다. 쓸 수 없는 명령어가 목록에 섞여 있으면 읽는 사람이
자기에게 해당하는 줄을 골라내야 하고, 그러면서 서버에 어떤 위험한 문이
있는지까지 알게 됩니다.

📨 업데이트 안내
이 메시지처럼, 요약만 펼쳐 보내고 자세한 내용은 접어서 보냅니다.""",
    ),
    # No fold, because there is nothing behind it. 4.0.0 and 4.0.1 added
    # features and were numbered as if they had fixed bugs; this corrects the
    # number without pretending the correction is news. The one line about it
    # exists only so the jump from 4.0.1 does not read as a version gone
    # missing.
    "4.1.0": ReleaseNote(
        headline="""• 새로 생긴 기능은 없습니다. 지난 안내에서 소개해 드린 그대로입니다.
• 그동안 기능이 늘어난 만큼 번호를 4.1.0 으로 바로잡았습니다.""",
    ),
    "4.1.1": ReleaseNote(
        headline="""• 한글로 답하면 아무 반응이 없던 문제를 고쳤습니다.""",
        detail="""⌨️ 한글 입력
역 이름처럼 한글로 답해야 하는 자리에서 봇이 답을 받고도 아무 말이 없던
문제입니다. 오류 메시지조차 없어서 봇이 멈춘 것처럼 보였고, 다시 보내도
마찬가지였습니다. 이제 한글로 답한 것도 다른 답과 똑같이 처리됩니다.""",
    ),
    # Keyed the way packaging normalises it, which is the form the lookup
    # arrives in - "4.2.0-beta.1" as declared would never be found.
    "4.2.0b1": ReleaseNote(
        headline="""• 코레일에 더해 SRT 열차도 검색하고 예약할 수 있습니다.
• 예약을 시작할 때 철도사를 먼저 고르고, 각 철도 계정으로 로그인합니다.
• 실제 SRT 예약 성공 경로를 확인 중인 베타 버전입니다.""",
        detail="""🚄 SRT 예약
SRT를 고르면 SRT가 정차하는 역과 열차에 맞춰 검색합니다. 코레일과 SRT
계정은 서로 섞이지 않으며, 즐겨찾기도 저장한 철도사로 다시 검색합니다.

🧪 베타 안내
열차 조회와 대화 흐름은 확인했지만 SRT 로그인 성공, 실제 예약과 취소는 아직
실계정으로 확인하는 중입니다. 예약 알림을 받으면 SRT 예약 목록을 직접 확인하고,
검증용 예약은 바로 취소해 주세요.""",
    ),
    "4.2.0": ReleaseNote(
        headline="""• 코레일에 더해 SRT 열차도 검색하고 예약할 수 있습니다.
• 예약을 시작할 때 어느 철도로 갈지 먼저 고릅니다.""",
        detail="""🚄 SRT
SRT를 고르면 SR이 정차하는 역만 받습니다. 열차 종류는 묻지 않습니다 - SR이
운행하는 열차가 SRT 하나뿐이라 고를 것이 없기 때문입니다.

🔐 철도별 계정
코레일과 SR은 별개의 회사라 계정도 따로입니다. 한쪽에 등록해 두었다고 다른
쪽으로 로그인하지 않습니다. 각각 한 번씩 등록해 두면 다음부터는 철도만
고르면 됩니다.

⭐ 즐겨찾기
저장해 둔 구간은 저장할 때의 철도로 다시 검색합니다.

⌨️ 한글 입력
역 이름처럼 한글로 답하는 자리에서 봇이 아무 말도 하지 않던 문제를
고쳤습니다. (4.1.1 에서 먼저 나갔습니다)""",
    ),
    "4.2.1": ReleaseNote(
        headline="""• 오래 도는 검색이 멈추면서 역 이름을 확인하라는 엉뚱한 안내를 보내던
  문제를 고쳤습니다. 이제 접속이 풀려도 스스로 다시 접속해 검색을 이어갑니다.""",
    ),
    "4.2.2": ReleaseNote(
        headline="""• 예매하다 그만둔 것이 있으면 /start 에서 이어서 할지 물어봅니다.
• /status 가 결제 기다리는 예약도 보여주고, 거기서 바로 취소할 수 있습니다.
• 결제하시면 봇이 확인해서 알려드립니다. 채팅에서 따로 하실 일은 없습니다.
• 결제 재촉 알림은 /notify_off 로 끌 수 있습니다.""",
        detail="""↩️ 하던 예매 이어서 하기
날짜까지 고르고 나갔다가 다시 /start 를 누르면 어디까지 답하셨는지 보여드리고
이어서 할지 처음부터 할지 고르실 수 있습니다. 예전에는 말없이 처음부터
시작해서 답해둔 것이 전부 사라졌습니다.

💳 결제 기다리는 예약 (/status)
좌석을 잡은 뒤에도 /status 가 "진행중인 예약이 없습니다" 라고 답하던 것을
고쳤습니다. 이제 어떤 열차인지, 예약번호와 결제 기한이 언제인지 함께 보여주고,
마음이 바뀌면 같은 화면의 버튼으로 취소할 수 있습니다. 취소는 철도사에 실제로
요청해서 확인된 것만 취소됐다고 말씀드립니다.

✅ 결제 확인
결제하시면 봇이 철도사에 확인해서 몇 초 안에 알려드립니다. 봇을 껐다 켜도,
좌석을 여러 개 잡는 중이어도 확인은 계속됩니다. 좌석을 여러 개 예약하실 때는
한 좌석 결제가 확인되면 다음 좌석 예약이 저절로 이어집니다.

🔕 알림 끄기 (/notify_off)
예약 뒤에 아무 메시지나 보내면 "결제했다"는 뜻으로 읽던 것을 없앴습니다.
결제하지 않았는데 좌석을 놓치거나, 결제했는데 계속 재촉받는 일이 없어집니다.
재촉이 성가시면 /notify_off 로 끄시면 됩니다 — 알림만 멈추고 결제 확인은
계속되므로 결제하시면 그때 알려드립니다.

⏱️ 재촉 간격
미결제 알림을 1분마다에서 30초마다로 바꿨습니다. 결제 기한이 10분이라
1분은 놓치기 쉬웠습니다.""",
    ),
    "4.3.0": ReleaseNote(
        headline="""• /start 에서 날짜·구간·시간·좌석을 한 화면에 설정할 수 있습니다.
• 예전처럼 채팅에서 하나씩 고르는 방법도 그대로 쓸 수 있습니다.""",
        detail="""📱 한 화면 예약 설정
계정을 한 번 등록한 뒤 /start 를 누르면 "예약 화면 열기" 버튼이 나옵니다.
날짜, 출발역과 도착역, 검색 시간대, 열차와 좌석 조건, 인원을 한 번에 고른 뒤
감시할 열차와 시작 여부만 채팅에서 확인합니다.

🔐 계정과 결제 정보
새 예약 화면에서는 철도 계정이나 결제 정보를 받지 않습니다. 등록해 둔 계정은
봇이 이전처럼 사용하고, 좌석을 잡은 뒤 결제도 이전처럼 직접 하시면 됩니다.

💬 채팅으로 예약
"채팅으로 예약"을 누르면 익숙한 버튼을 하나씩 고르는 방식으로 진행합니다.
새 화면을 쓰지 않아도 기존 기능은 달라지지 않습니다.""",
    ),
    "4.3.1": ReleaseNote(
        headline="""• 채팅 아래의 "예약 열기"로 예약 화면을 바로 열 수 있습니다.
• 예약 화면에서 코레일과 SRT를 자유롭게 고를 수 있습니다.""",
        detail="""🚄 코레일·SRT 선택
어느 철도 계정을 먼저 등록했는지와 상관없이 예약 화면에서 코레일과 SRT를
모두 고를 수 있습니다. 고른 철도 계정이 아직 없으면 날짜와 구간을 잃지 않고
채팅에서 계정만 등록한 뒤 열차 선택으로 이어집니다.

📱 예약 화면 바로 열기
채팅 입력창의 "예약 열기"를 누르면 /start 를 먼저 입력하지 않아도 됩니다.
봇 프로필에 앱이 연결된 경우에는 프로필의 "앱 열기"에서도 같은 화면을
사용할 수 있습니다.

🔐 계정 정보
예약 화면에서는 철도 계정이나 결제 정보를 받지 않습니다. 필요한 계정 등록은
이전처럼 봇과의 비공개 채팅에서만 진행합니다.""",
    ),
}


def notes_for(version: str) -> ReleaseNote | None:
    """
    The notes for a version, if any were written.

    Args:
        version: The version being announced

    Returns:
        The note, or None when the release shipped without one
    """
    return NOTES.get(version)
