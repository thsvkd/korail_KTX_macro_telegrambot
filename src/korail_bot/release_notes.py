"""
What each release changed, in the words the users get to read.

The bot announces itself after an update, and an announcement that only names
a number is noise: nobody acts on "v4.0.0 이 되었습니다". These are the lines
that make it worth the interruption, so they are written for the person who
books tickets rather than for whoever wrote the diff - no module names, no
refactors, nothing they cannot see from the chat.

Releasing means bumping korail_bot.__version__ and adding the entry here in
the same commit. A version with no entry is still announced, plainly; that is
the fallback, not the intent.
"""

#: Version -> the highlights, one per line, already written as bullets.
NOTES: dict[str, str] = {
    "4.0.0": """• 앞 단계로 돌아가는 ◀️ 뒤로 버튼이 생겼습니다.
  날짜를 하루 잘못 골랐다고 처음부터 다시 하지 않아도 됩니다.
• /notify 로 켜두면 검색이 도는 동안 진행 상황을 알려줍니다. (기본 꺼짐)
• /fav 로 자주 타는 구간을 저장해두고 한 번에 불러올 수 있습니다.
• "모든 열차" 가 무엇을 포함하는지, 무엇을 조심해야 하는지 안내합니다.""",
}


def notes_for(version: str) -> str | None:
    """
    The highlights for a version, if any were written.

    Args:
        version: The version being announced

    Returns:
        The bullets, or None when the release shipped without them
    """
    return NOTES.get(version)
