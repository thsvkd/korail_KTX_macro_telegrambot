"""Which railway a search is against.

The bot used to know one company, so nothing had to say which. Now that there
are two, everything that describes a search - the stations it may name, the
train types it may ask for, the credentials it logs in with - depends on the
answer, and the answer has to travel with the search from the first question
to the reservation.

Records written before this existed carry no operator at all. Those are
Korail searches, because Korail was the only thing there was, and
:meth:`Operator.parse` is where that is decided rather than in each of the
dozen places that read one back.
"""

from enum import StrEnum

from SRT.constants import STATION_CODE as SRT_STATION_CODE


class Operator(StrEnum):
    """A railway the bot can book with."""

    KORAIL = "korail"
    SRT = "srt"

    @classmethod
    def parse(cls, value: object) -> "Operator":
        """
        Read an operator out of whatever was stored.

        Missing, empty, and unrecognised all come back as Korail. The first
        two because every record written before SRT existed is a Korail
        search; the last because an operator nobody recognises is a bug in
        something else, and refusing to run at all would strand a search the
        user is waiting on rather than fix it.

        Args:
            value: An Operator, its value, or nothing at all

        Returns:
            The operator to treat the record as belonging to
        """
        if isinstance(value, cls):
            return value
        if not value:
            return cls.KORAIL
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.KORAIL

    @classmethod
    def from_answer(cls, text: object) -> "Operator | None":
        """
        Read an operator out of what someone pressed or typed.

        Unlike :meth:`parse`, an answer nobody recognises comes back as None
        rather than as Korail. The two are reading different things: parse is
        reading a stored record, where guessing wrong at worst mislabels a
        search already under way, while this is reading a question just asked,
        and answering "에스알티" with a Korail booking would be getting it
        wrong without saying so.

        Args:
            text: What the user pressed or typed

        Returns:
            The operator, or None when the answer is not one
        """
        return ANSWER_ALIASES.get(str(text or "").strip().lower())

    @property
    def display_name(self) -> str:
        """What to call this operator when talking to the user."""
        return "코레일" if self is Operator.KORAIL else "SRT"

    @property
    def offers_train_types(self) -> bool:
        """
        Whether asking "which kind of train" means anything here.

        Korail runs KTX beside 무궁화호 and the rest, so the question decides
        what the search will settle for. SR runs SRT and nothing else, and
        asking would be offering a choice with one answer.
        """
        return self is Operator.KORAIL

    @property
    def reports_seats_before_payment(self) -> bool:
        """
        Whether this railway says which seat a booking got before it is paid for.

        The question behind every seat condition. SR fills the seat and
        carriage in as soon as the booking exists, so a search can look at
        what it won and give it back if it is not what was asked for. Korail
        reports a seat number only on a paid ticket, and this bot never pays -
        so a Korail search cannot check its own work, and is not offered the
        choice rather than being offered one that quietly does nothing.
        """
        return self is Operator.SRT

    @property
    def major_stations(self) -> tuple[str, ...]:
        """The stations worth putting on buttons for this operator."""
        return KORAIL_MAJOR_STATIONS if self is Operator.KORAIL else SRT_MAJOR_STATIONS

    def serves(self, station: str) -> bool | None:
        """
        Whether this operator stops at a station.

        SR publishes its whole list - 30-odd stations, fixed - so the answer
        is definite. Korail's list is fetched and cached elsewhere and is far
        too long to duplicate here, so this declines to answer for it and
        leaves that check where it already lives.

        Args:
            station: Station name, without the trailing '역'

        Returns:
            True or False for SR; None for Korail, meaning "ask elsewhere"
        """
        if self is Operator.KORAIL:
            return None
        return station.strip() in SRT_STATIONS


#: What an answer to "which railway?" may look like. The buttons carry the
#: enum values; the rest are what someone typing instead of pressing would
#: write. Lower-cased on both sides, so only lower-case keys belong here.
ANSWER_ALIASES = {
    "korail": Operator.KORAIL,
    "코레일": Operator.KORAIL,
    "ktx": Operator.KORAIL,
    "srt": Operator.SRT,
    "sr": Operator.SRT,
    "에스알티": Operator.SRT,
    "수서고속철도": Operator.SRT,
}

#: Every station SR stops at, taken from the client library rather than copied
#: here - a list this repository maintained separately would be a list that
#: drifts. Note that it holds both 경주 and its old name 신경주.
SRT_STATIONS = frozenset(SRT_STATION_CODE)

#: The ones worth a button. Ordered roughly by how much traffic they carry,
#: since that is the order someone scans them in.
SRT_MAJOR_STATIONS = (
    "수서",
    "동탄",
    "평택지제",
    "천안아산",
    "오송",
    "대전",
    "동대구",
    "부산",
    "울산(통도사)",
    "포항",
    "광주송정",
    "목포",
    "익산",
    "전주",
    "여수EXPO",
    "창원중앙",
    "진주",
    "경주",
)

#: Korail's, kept here beside SR's so the two are read together. Unchanged
#: from the list the station keyboard has always offered - the order is the
#: one users have been scanning, and this is not the change to reshuffle it.
KORAIL_MAJOR_STATIONS = (
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
