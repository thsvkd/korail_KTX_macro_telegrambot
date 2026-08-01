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
# The password prompt has no answer that could go on a button, and exists as a
# step only so the "go back" offered there can be told from a stale press. Its
# keyboard carries the BACK sentinel and nothing else.
STEP_PASSWORD = "pw"
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
# Offered with the news that a search died, not as part of the conversation.
STEP_DEAD = "dd"
# Whether to replace an already registered Korail account.
STEP_ONBOARD_OVERWRITE = "obw"
# Asking the operator for continued access, once the trial runs out.
STEP_ACCESS = "ac"
# The operator answering those requests, and managing who is approved.
STEP_APPROVE = "ap"
STEP_USERS = "us"

# Answers to STEP_CONFIRM that are neither yes nor no.
CONFIRM_SCHEDULE = "*schedule"

# Not an answer at all: the user wants the previous question back.
#
# One value across every step, so there is one rule in the handler rather than
# a sentinel per keyboard. The leading * keeps it from ever being mistaken for
# an answer - no station, time, digit or date starts with one.
BACK = "*back"

# Answers to STEP_TRAIN_SELECT that are not a train number. Prefixed so they
# cannot collide with one: Korail numbers its trains in digits.
TRAIN_SELECT_DONE = "*done"
TRAIN_SELECT_ALL = "*all"
TRAIN_SELECT_REFRESH = "*refresh"

# Answers to STEP_DEAD: start the same search again, or be done with it.
DEAD_RESUME = "resume"
DEAD_DISCARD = "discard"

# Answers to STEP_ACCESS.
ACCESS_ASK = "ask"
ACCESS_DISMISS = "dismiss"

# Prefixes for the operator's lists. The rest of the value is a phone hash,
# which is 32 hex characters - well inside the 64-byte callback_data limit.
APPROVE_PICK = "p:"
APPROVE_YES = "y:"
APPROVE_NO = "n:"
APPROVE_CLOSE = "*close"
APPROVE_BACK = "*back"

USERS_PICK = "p:"
USERS_REVOKE = "r:"
USERS_CLOSE = "*close"
USERS_BACK = "*back"

# The progress state at which each step's answer is expected.
#
# Buttons stay tappable in the chat history forever, so the only thing telling
# a fresh press from one on a message five steps back is which question it
# belongs to. Without this every value here is a bare digit that the current
# step would happily accept: tapping an old "특실만" would be read as "4명".
STEP_PROGRESS = {
    STEP_START_CONFIRM: UserProgress.STARTED,
    STEP_PASSWORD: UserProgress.ID_INPUT_SUCCESS,
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
    STEP_ONBOARD_OVERWRITE: UserProgress.ONBOARDING_OVERWRITE_PENDING,
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


def _back_row(step: str) -> list[dict]:
    """
    The way to the question before this one.

    On every step that has one. A flow this long is answered wrongly now and
    then - a station picked in a hurry, a date off by a day - and without this
    the only remedy is cancelling and typing all of it again.
    """
    return [_button("◀️ 뒤로", step, BACK)]


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
    """
    For the phone number, which is typed and has nothing behind it.

    The welcome message is the only step before it, and going back to a
    question already answered with "yes, go ahead" is not worth a button.
    """
    return _keyboard(_cancel_row())


def password_keyboard() -> InlineKeyboard:
    """
    For the password, which is typed but does have something behind it.

    A mistyped phone number is only discovered here - the login fails and the
    number is on screen in the failure message - so this is exactly where the
    way back to it is worth having.
    """
    return _keyboard(_back_row(STEP_PASSWORD), _cancel_row())


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

    No way back from here: this is the first question of the booking, and what
    lies behind it is a login that has already succeeded.
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
    return _keyboard(*_rows(buttons, 3), [_manual_button(step)], _back_row(step), _cancel_row())


def time_keyboard(step: str, include_unlimited: bool = False) -> InlineKeyboard:
    """
    Whole hours as buttons.

    Hour granularity on purpose: the answer bounds a search window, and
    nobody means "no earlier than 14:07". Anyone who does can still type it.
    """
    unlimited = [_button("⏰ 제한 없음 (24시)", step, "2400")] if include_unlimited else []
    hours = [_button(f"{hour:02d}시", step, f"{hour:02d}00") for hour in range(24)]

    return _keyboard(
        unlimited, *_rows(hours, 6), [_manual_button(step)], _back_row(step), _cancel_row()
    )


def train_type_keyboard() -> InlineKeyboard:
    """KTX only, or everything."""
    return _keyboard(
        [_button("🚅 KTX·KTX-산천만", STEP_TRAIN_TYPE, "1")],
        [_button("🚂 모든 열차", STEP_TRAIN_TYPE, "2")],
        _back_row(STEP_TRAIN_TYPE),
        _cancel_row(),
    )


def seat_option_keyboard() -> InlineKeyboard:
    """The four ReserveOption values, in the order the typed flow numbers them."""
    return _keyboard(
        [_button("1️⃣ 일반실 우선", STEP_SEAT_OPTION, "1")],
        [_button("2️⃣ 일반실만", STEP_SEAT_OPTION, "2")],
        [_button("3️⃣ 특실 우선", STEP_SEAT_OPTION, "3")],
        [_button("4️⃣ 특실만", STEP_SEAT_OPTION, "4")],
        _back_row(STEP_SEAT_OPTION),
        _cancel_row(),
    )


def passenger_count_keyboard() -> InlineKeyboard:
    """One through nine - the range validate_passenger_count accepts."""
    buttons = [_button(f"{count}명", STEP_PASSENGER_COUNT, str(count)) for count in range(1, 10)]
    return _keyboard(*_rows(buttons, 3), _back_row(STEP_PASSENGER_COUNT), _cancel_row())


def seat_strategy_keyboard() -> InlineKeyboard:
    """Seats together, or seats at all."""
    return _keyboard(
        [_button("🪑 연속 좌석 (권장)", STEP_SEAT_STRATEGY, "1")],
        [_button("🎲 랜덤 배치 (성공률 ↑)", STEP_SEAT_STRATEGY, "2")],
        _back_row(STEP_SEAT_STRATEGY),
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
    rows.append(_back_row(STEP_TRAIN_SELECT))
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
        _back_row(STEP_CONFIRM),
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
        _back_row(STEP_SCHEDULE),
        _cancel_row(),
    )


def onboarding_start_keyboard() -> InlineKeyboard:
    """
    The way into registering a Korail account.

    Same shape as the welcome confirmation, and deliberately so: registering
    is what "yes, go ahead" means for someone who has not done it yet.
    """
    return _keyboard(
        [_button("🔑 계정 등록 시작", STEP_START_CONFIRM, "Y")],
        [_button("❌ 그만두기", STEP_START_CONFIRM, "N")],
    )


def onboarding_overwrite_keyboard() -> InlineKeyboard:
    """
    Whether to replace an account that is already registered.

    Registering again throws away a working login, so it is confirmed rather
    than done on the way past - and "keep" is the safe answer, so it is the
    one that needs no thought.
    """
    return _keyboard(
        [_button("🔄 다시 등록", STEP_ONBOARD_OVERWRITE, "Y")],
        [_button("❌ 그대로 두기", STEP_ONBOARD_OVERWRITE, "N")],
    )


def access_request_keyboard(pending: bool = False) -> InlineKeyboard:
    """
    The way out of a used-up trial.

    Someone who tried the bot and hit the wall should be one press away from
    being let in, rather than reading an instruction to contact a stranger by
    some means the bot never mentions.
    """
    if pending:
        return _keyboard([_button("⏳ 요청 처리를 기다리는 중", STEP_ACCESS, ACCESS_DISMISS)])
    return _keyboard(
        [_button("🙋 사용 승인 요청", STEP_ACCESS, ACCESS_ASK)],
        [_button("❌ 닫기", STEP_ACCESS, ACCESS_DISMISS)],
    )


def approve_list_keyboard(requests: list) -> InlineKeyboard:
    """
    Pending access requests, one per row.

    Args:
        requests: AccessRequest objects, oldest first

    Returns:
        The keyboard the operator picks from
    """
    rows = [
        [
            _button(
                f"{request.masked_phone}  ·  {request.requested_at:%m/%d %H:%M}",
                STEP_APPROVE,
                f"{APPROVE_PICK}{request.phone_hash}",
            )
        ]
        for request in requests
    ]
    rows.append([_button("❌ 닫기", STEP_APPROVE, APPROVE_CLOSE)])
    return _keyboard(*rows)


def approve_decision_keyboard(phone_hash: str) -> InlineKeyboard:
    """Approve or turn down one request."""
    return _keyboard(
        [
            _button("✅ 승인", STEP_APPROVE, f"{APPROVE_YES}{phone_hash}"),
            _button("🚫 거절", STEP_APPROVE, f"{APPROVE_NO}{phone_hash}"),
        ],
        [_button("◀️ 뒤로", STEP_APPROVE, APPROVE_BACK)],
    )


def users_list_keyboard(users: list) -> InlineKeyboard:
    """
    Approved users, one per row.

    Args:
        users: ApprovedUser objects, most recent first

    Returns:
        The keyboard the operator picks from
    """
    rows = [
        [
            _button(
                f"{user.masked_phone}  ·  {user.approved_at:%m/%d}",
                STEP_USERS,
                f"{USERS_PICK}{user.phone_hash}",
            )
        ]
        for user in users
    ]
    rows.append([_button("❌ 닫기", STEP_USERS, USERS_CLOSE)])
    return _keyboard(*rows)


def users_revoke_keyboard(phone_hash: str) -> InlineKeyboard:
    """Confirm withdrawing one approval."""
    return _keyboard(
        [_button("🚫 승인 취소", STEP_USERS, f"{USERS_REVOKE}{phone_hash}")],
        [_button("◀️ 뒤로", STEP_USERS, USERS_BACK)],
    )


def dead_search_keyboard(resumable: bool = True) -> InlineKeyboard:
    """
    What to do about a search that stopped on its own.

    Offered with the message announcing the death rather than left to the
    user to work out: they were waiting on a search, and the two things worth
    doing about it are doing it again and letting it go.

    The resume button is left off when there is no stored login to resume
    with - a button that can only fail is worse than its absence.

    Not part of the conversation, so it carries no progress state and the
    router handles it before the staleness check. A search that died stays
    dead however long the message sits unread, and the answer to it is just
    as valid an hour later.
    """
    rows = []
    if resumable:
        rows.append([_button("🔄 같은 조건으로 재개", STEP_DEAD, DEAD_RESUME)])
    rows.append([_button("🗑️ 그만두기", STEP_DEAD, DEAD_DISCARD)])
    return _keyboard(*rows)


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
