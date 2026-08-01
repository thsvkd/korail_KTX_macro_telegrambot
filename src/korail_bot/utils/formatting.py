"""Small formatters shared by the messages the bot sends."""


def format_duration(seconds: float) -> str:
    """
    A span of time, the way someone would say it out loud.

    Coarse on purpose. A search that has been running for three hours is
    reported as "3시간 12분", not to the second: the number is there to give a
    sense of how long the wait has been, and a ticking seconds field would
    only invite the reader to watch it.

    Args:
        seconds: How long, in seconds. Negative is treated as none at all,
            which is what a clock read out of order should look like.

    Returns:
        Something like "3시간 12분", "12분", or "1분 미만"
    """
    total_minutes = int(max(0.0, seconds) // 60)
    hours, minutes = divmod(total_minutes, 60)

    if hours and minutes:
        return f"{hours}시간 {minutes}분"
    if hours:
        return f"{hours}시간"
    if minutes:
        return f"{minutes}분"
    return "1분 미만"
