"""
Inline keyboards for the reservation conversation.

Every step whose answer comes from a known set is offered as buttons. The
value a button carries is exactly what the user would otherwise have typed,
so a press and a typed answer reach the same handler and go through the same
validation - there is one state machine, not two.

Steps that take free text (phone number, password, a station or time that is
not on the list) stay typed. Buttons there would either be a lie or a list of
every station in the country.
"""

from datetime import datetime, timedelta

from korail_bot.models import UserProgress

# Telegram rejects callback_data over 64 bytes, measured after UTF-8 encoding
# - so a Korean station name costs three bytes a character. Nothing built here
# comes close, and a test holds that line.
CALLBACK_DATA_MAX_BYTES = 64

# Callback data is "<step>:<value>". The step says which question is being
# answered; the value is the answer, in the form the typed flow expects.
STEP_START_CONFIRM = "st"
STEP_DATE = "dt"
STEP_SRC_STATION = "src"
STEP_DST_STATION = "dst"
STEP_DEP_TIME = "t1"
STEP_MAX_DEP_TIME = "t2"
STEP_TRAIN_TYPE = "tt"
STEP_SEAT_OPTION = "so"
STEP_PASSENGER_COUNT = "pc"
STEP_SEAT_STRATEGY = "ss"
STEP_TRAIN_SELECT = "trs"
STEP_CONFIRM = "cf"
STEP_SCHEDULE = "sch"
STEP_CANCEL = "x"

# Answers to STEP_CONFIRM that are neither yes nor no.
CONFIRM_SCHEDULE = "*schedule"

# Answers to STEP_SCHEDULE that are not a time. A real one is all digits.
SCHEDULE_BACK = "*back"

# Answers to STEP_TRAIN_SELECT that are not a train number. Prefixed so they
# cannot collide with one: Korail numbers its trains in digits.
TRAIN_SELECT_DONE = "*done"
TRAIN_SELECT_ALL = "*all"
TRAIN_SELECT_REFRESH = "*refresh"

# The progress state at which each step's answer is expected.
#
# Buttons stay tappable in the chat history forever, so the only thing telling
# a fresh press from one on a message five steps back is which question it
# belongs to. Without this every value here is a bare digit that the current
# step would happily accept: tapping an old "특실만" would be read as "4명".
STEP_PROGRESS = {
    STEP_START_CONFIRM: UserProgress.STARTED,
    STEP_DATE: UserProgress.PW_INPUT_SUCCESS,
    STEP_SRC_STATION: UserProgress.DATE_INPUT_SUCCESS,
    STEP_DST_STATION: UserProgress.SRC_LOCATE_INPUT_SUCCESS,
    STEP_DEP_TIME: UserProgress.DST_LOCATE_INPUT_SUCCESS,
    STEP_MAX_DEP_TIME: UserProgress.DEP_TIME_INPUT_SUCCESS,
    STEP_TRAIN_TYPE: UserProgress.MAX_DEP_TIME_INPUT_SUCCESS,
    STEP_SEAT_OPTION: UserProgress.TRAIN_TYPE_INPUT_SUCCESS,
    STEP_PASSENGER_COUNT: UserProgress.SPECIAL_INPUT_SUCCESS,
    STEP_SEAT_STRATEGY: UserProgress.PASSENGER_COUNT_INPUT_SUCCESS,
    STEP_TRAIN_SELECT: UserProgress.SEAT_STRATEGY_INPUT_SUCCESS,
    STEP_CONFIRM: UserProgress.TRAIN_SELECT_INPUT_SUCCESS,
    STEP_SCHEDULE: UserProgress.SCHEDULE_INPUT_PENDING,
}

# Steps whose question survives being answered.
#
# Ticking a train off a list is not the end of the question - the user is
# expected to tick several. The keyboard stays, and the handler redraws it
# with the new ticks rather than the router clearing it away.
REPEATABLE_STEPS = frozenset({STEP_TRAIN_SELECT})

# Chosen instead of an answer: the user wants to type this one. Not a value
# any step could produce, so it can never collide with a real answer.
MANUAL = "manual"

# Offered when a station is asked for. Every name here has to be one Korail
# knows, so they are taken from the station table and a test checks them
# against it - a button that fails validation would be worse than no button.
MAJOR_STATIONS = (
    "서울",
    "용산",
    "청량리",
    "수서",
    "광명",
    "천안아산",
    "오송",
    "대전",
    "동대구",
    "부산",
    "울산(통도사)",
    "포항",
    "익산",
    "전주",
    "광주송정",
    "목포",
    "여수EXPO",
    "강릉",
)

# How many days ahead the date buttons reach. A week and a bit: far enough to
# cover the trip people are actually booking, short enough to stay one screen.
DATE_QUICK_DAYS = 9

_WEEKDAYS = "월화수목금토일"

InlineKeyboard = dict


def _button(text: str, step: str, value: str) -> dict:
    """One inline button carrying an answer."""
    return {"text": text, "callback_data": f"{step}:{value}"}


def _manual_button(step: str) -> dict:
    """The way out of a keyboard that does not list what the user wants."""
    return _button("⌨️ 직접 입력", step, MANUAL)


def _cancel_row() -> list[dict]:
    """Present on every keyboard: leaving should never need a typed command."""
    return [_button("❌ 취소", STEP_CANCEL, "cancel")]


def _rows(buttons: list[dict], per_row: int) -> list[list[dict]]:
    """Lay buttons out in rows of at most per_row."""
    return [buttons[index : index + per_row] for index in range(0, len(buttons), per_row)]


def _keyboard(*rows: list[dict]) -> InlineKeyboard:
    """Wrap rows in the reply_markup shape the Bot API wants."""
    return {"inline_keyboard": [row for row in rows if row]}


def cancel_only_keyboard() -> InlineKeyboard:
    """For steps that must be typed - a phone number, a password, a password retry."""
    return _keyboard(_cancel_row())


def start_confirm_keyboard() -> InlineKeyboard:
    """Yes/no on the welcome message."""
    return _keyboard(
        [
            _button("✅ 예, 진행합니다", STEP_START_CONFIRM, "Y"),
            _button("❌ 아니오", STEP_START_CONFIRM, "N"),
        ]
    )


def date_keyboard(today: datetime | None = None) -> InlineKeyboard:
    """
    The next few days as buttons.

    Dates come from the local clock, the same one validate_date compares
    against, so a button can never offer a date that validation then calls
    past.
    """
    today = today or datetime.now()

    named = {0: "오늘", 1: "내일", 2: "모레"}
    buttons = []
    for offset in range(DATE_QUICK_DAYS):
        day = today + timedelta(days=offset)
        name = named.get(offset, f"{_WEEKDAYS[day.weekday()]}요일")
        buttons.append(_button(f"{name} {day.month}/{day.day}", STEP_DATE, day.strftime("%Y%m%d")))

    return _keyboard(*_rows(buttons, 3), [_manual_button(STEP_DATE)], _cancel_row())


def station_keyboard(step: str, exclude: str | None = None) -> InlineKeyboard:
    """
    The busy stations as buttons, with typing still available.

    exclude drops a station from the list - used so the arrival keyboard does
    not offer the station the user just picked as the departure.
    """
    buttons = [_button(station, step, station) for station in MAJOR_STATIONS if station != exclude]
    return _keyboard(*_rows(buttons, 3), [_manual_button(step)], _cancel_row())


def time_keyboard(step: str, include_unlimited: bool = False) -> InlineKeyboard:
    """
    Whole hours as buttons.

    Hour granularity on purpose: the answer bounds a search window, and
    nobody means "no earlier than 14:07". Anyone who does can still type it.
    """
    unlimited = [_button("⏰ 제한 없음 (24시)", step, "2400")] if include_unlimited else []
    hours = [_button(f"{hour:02d}시", step, f"{hour:02d}00") for hour in range(24)]

    return _keyboard(unlimited, *_rows(hours, 6), [_manual_button(step)], _cancel_row())


def train_type_keyboard() -> InlineKeyboard:
    """KTX only, or everything."""
    return _keyboard(
        [_button("🚅 KTX·KTX-산천만", STEP_TRAIN_TYPE, "1")],
        [_button("🚂 모든 열차", STEP_TRAIN_TYPE, "2")],
        _cancel_row(),
    )


def seat_option_keyboard() -> InlineKeyboard:
    """The four ReserveOption values, in the order the typed flow numbers them."""
    return _keyboard(
        [_button("1️⃣ 일반실 우선", STEP_SEAT_OPTION, "1")],
        [_button("2️⃣ 일반실만", STEP_SEAT_OPTION, "2")],
        [_button("3️⃣ 특실 우선", STEP_SEAT_OPTION, "3")],
        [_button("4️⃣ 특실만", STEP_SEAT_OPTION, "4")],
        _cancel_row(),
    )


def passenger_count_keyboard() -> InlineKeyboard:
    """One through nine - the range validate_passenger_count accepts."""
    buttons = [_button(f"{count}명", STEP_PASSENGER_COUNT, str(count)) for count in range(1, 10)]
    return _keyboard(*_rows(buttons, 3), _cancel_row())


def seat_strategy_keyboard() -> InlineKeyboard:
    """Seats together, or seats at all."""
    return _keyboard(
        [_button("🪑 연속 좌석 (권장)", STEP_SEAT_STRATEGY, "1")],
        [_button("🎲 랜덤 배치 (성공률 ↑)", STEP_SEAT_STRATEGY, "2")],
        _cancel_row(),
    )


def train_select_keyboard(options: list[dict], selected: list[str] | None = None) -> InlineKeyboard:
    """
    The trains running in the chosen window, each one tickable.

    A tick is not a decision to move on - several trains can be watched - so
    every train button leaves the keyboard in place and only its own tick
    changes. Finishing is a separate button.

    Args:
        options: Trains, as {"no": train number, "label": what to show,
                 "soldout": whether it has no seats right now}
        selected: Train numbers currently ticked

    Returns:
        The keyboard, with a row per train
    """
    ticked = set(selected or [])

    rows = []
    for option in options:
        number = option["no"]
        mark = "☑️" if number in ticked else "⬜"
        seats = "매진" if option.get("soldout") else "여석"
        rows.append([_button(f"{mark} {option['label']}  {seats}", STEP_TRAIN_SELECT, number)])

    if ticked:
        rows.append(
            [_button(f"▶️ 선택한 {len(ticked)}개 열차로 시작", STEP_TRAIN_SELECT, TRAIN_SELECT_DONE)]
        )
    rows.append([_button("🚄 시간대 전체 감시 (성공률 ↑)", STEP_TRAIN_SELECT, TRAIN_SELECT_ALL)])
    rows.append([_button("🔄 목록 새로고침", STEP_TRAIN_SELECT, TRAIN_SELECT_REFRESH)])
    rows.append(_cancel_row())

    return _keyboard(*rows)


def confirm_keyboard() -> InlineKeyboard:
    """
    The last stop before a search starts.

    Starting now is the first button and the default; booking a start time is
    the exception, and reads as one.
    """
    return _keyboard(
        [_button("🎯 지금 검색 시작", STEP_CONFIRM, "Y")],
        [_button("⏰ 시작 시각 예약", STEP_CONFIRM, CONFIRM_SCHEDULE)],
        [_button("❌ 취소", STEP_CONFIRM, "N")],
    )


def schedule_keyboard(now: datetime | None = None) -> InlineKeyboard:
    """
    Times worth starting a search at, as buttons.

    Two kinds. Relative offsets, for "not right now, I am doing something
    else". And the next few mornings at the hours ticket releases actually
    happen, for the case this feature exists to serve.

    Values are absolute, resolved here from the clock, so a press means the
    same moment however long it sits unpressed on the screen.
    """
    now = now or datetime.now()

    def at(moment: datetime, label: str) -> dict:
        return _button(label, STEP_SCHEDULE, moment.strftime("%Y%m%d%H%M"))

    relative = [at(now + timedelta(hours=hours), f"{hours}시간 뒤") for hours in (1, 3, 6)]

    # Korail opens holiday booking in the small hours of the morning, and
    # cancellations turn up when people wake up and change their minds.
    clock_times = []
    for days, hour in ((0, 22), (1, 6), (1, 7), (1, 9), (2, 6), (2, 7)):
        moment = (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if moment <= now:
            # An hour that has already gone by today is not an offer.
            continue
        day = {0: "오늘", 1: "내일", 2: "모레"}[days]
        clock_times.append(at(moment, f"{day} {hour:02d}:00"))

    return _keyboard(
        relative,
        *_rows(clock_times, 3),
        [_manual_button(STEP_SCHEDULE)],
        [_button("◀️ 뒤로", STEP_SCHEDULE, SCHEDULE_BACK)],
        _cancel_row(),
    )


def empty_keyboard() -> InlineKeyboard:
    """
    What replaces a keyboard once its question has been answered.

    An answered question's buttons stay pressable otherwise, and a second
    press is never something the user meant.
    """
    return {"inline_keyboard": []}


def button_label(reply_markup: dict | None, callback_data: str) -> str | None:
    """
    The text on the button that produced this callback data.

    Telegram sends the pressed message's markup back with the callback, so
    the label is read from there rather than kept in a second table that
    could drift out of step with the keyboards above.
    """
    if not isinstance(reply_markup, dict):
        return None

    for row in reply_markup.get("inline_keyboard") or []:
        if not isinstance(row, list):
            continue
        for button in row:
            if isinstance(button, dict) and button.get("callback_data") == callback_data:
                text = button.get("text")
                return text if isinstance(text, str) else None
    return None
