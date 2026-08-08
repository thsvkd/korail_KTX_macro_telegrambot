"""
What "I want an aisle seat near the front" means once a railway answers.

The preference is the only thing standing between a user's condition and a
search that gives back seats it should have kept, so the cases that matter
here are the ones where the answer is not obviously yes or no: a label in an
unexpected shape, a half-open row range, a record written before any of this
existed.
"""

import pytest

from korail_bot.models import SeatPreference, TrainSearchParams, parse_seat_label


class TestParseSeatLabel:
    """Reading a railway's seat label."""

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("3A", (3, "A")),
            ("12D", (12, "D")),
            # Padded and lower-cased forms are the same seat. A railway that
            # starts zero-padding tomorrow must not silently stop matching.
            ("03A", (3, "A")),
            ("3a", (3, "A")),
            (" 7C ", (7, "C")),
            ("7 C", (7, "C")),
        ],
    )
    def test_reads_row_and_column(self, label, expected):
        assert parse_seat_label(label) == expected

    @pytest.mark.parametrize(
        "label",
        [
            None,
            "",
            # Shapes this deliberately does not guess at.
            "A3",
            "3",
            "A",
            "3AB",
            "입석",
        ],
    )
    def test_unreadable_labels_are_none(self, label):
        assert parse_seat_label(label) is None


class TestSeatPreferenceMatching:
    """Whether an assigned seat is one that was asked for."""

    def test_empty_preference_accepts_anything(self):
        preference = SeatPreference()

        assert preference.is_empty()
        assert preference.matches("3A")
        assert preference.matches("19D")
        assert preference.matches(None)

    def test_column_set_admits_only_its_letters(self):
        preference = SeatPreference(columns=("A", "D"))

        assert preference.matches("3A")
        assert preference.matches("12D")
        assert not preference.matches("3B")
        assert not preference.matches("12C")

    def test_row_range_is_inclusive_at_both_ends(self):
        preference = SeatPreference(row_min=5, row_max=10)

        assert preference.matches("5A")
        assert preference.matches("10A")
        assert not preference.matches("4A")
        assert not preference.matches("11A")

    def test_half_open_ranges_leave_the_other_end_alone(self):
        from_five = SeatPreference(row_min=5)
        up_to_five = SeatPreference(row_max=5)

        assert from_five.matches("99A") and not from_five.matches("4A")
        assert up_to_five.matches("1A") and not up_to_five.matches("6A")

    def test_column_and_row_must_both_hold(self):
        preference = SeatPreference(columns=("A",), row_min=1, row_max=3)

        assert preference.matches("2A")
        # Right column, wrong row - and the other way round.
        assert not preference.matches("9A")
        assert not preference.matches("2B")

    def test_unreadable_label_is_accepted_rather_than_given_back(self):
        """
        A label this cannot read must not be read as a mismatch.

        The caller answers False by cancelling the booking, so treating an
        unexpected shape as "not what you asked for" would turn one parsing
        surprise into a search that throws away every seat it ever wins.
        """
        preference = SeatPreference(columns=("A",), row_min=1, row_max=3)

        assert preference.matches("입석")
        assert preference.matches(None)
        assert preference.matches("")


class TestSeatPreferenceEncoding:
    """The single flattened form argv and Redis both carry."""

    @pytest.mark.parametrize(
        "preference,encoded",
        [
            (SeatPreference(), ""),
            (SeatPreference(columns=("A", "D")), "A,D:"),
            (SeatPreference(row_min=1, row_max=15), ":1-15"),
            (SeatPreference(columns=("A", "D"), row_min=1, row_max=15), "A,D:1-15"),
            (SeatPreference(row_min=5), ":5-"),
            (SeatPreference(row_max=5), ":-5"),
        ],
    )
    def test_round_trips(self, preference, encoded):
        assert preference.encode() == encoded
        assert SeatPreference.decode(encoded) == preference

    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            # How a search started by a build that predates seat picking
            # arrives, and how a record written before it reads back.
            "consecutive",
            "쓰레기",
        ],
    )
    def test_unreadable_text_means_no_preference(self, text):
        assert SeatPreference.decode(text).is_empty()

    def test_decode_drops_letters_that_are_not_columns(self):
        assert SeatPreference.decode("A,Z,D,:").columns == ("A", "D")

    def test_decode_tolerates_spacing_and_case(self):
        assert SeatPreference.decode(" a , d : 1 - 15 ") == SeatPreference(
            columns=("A", "D"), row_min=1, row_max=15
        )


class TestSeatPreferenceDescription:
    """How the preference reads back to the person who set it."""

    @pytest.mark.parametrize(
        "preference,text",
        [
            (SeatPreference(), "지정 없음"),
            (SeatPreference(columns=("A", "D")), "A·D열"),
            (SeatPreference(columns=("A", "D"), row_min=1, row_max=15), "A·D열 1~15번"),
            (SeatPreference(row_min=5), "5번 이상"),
            (SeatPreference(row_max=5), "5번 이하"),
        ],
    )
    def test_describes_itself_in_korean(self, preference, text):
        assert preference.describe() == text


class TestSearchParamsCarryThePreference:
    """The search parameters hold it encoded, and read it back as an object."""

    @staticmethod
    def _params(**kwargs) -> TrainSearchParams:
        return TrainSearchParams(
            dep_date="20260810",
            src_locate="서울",
            dst_locate="부산",
            dep_time="080000",
            **kwargs,
        )

    def test_defaults_to_no_preference(self):
        params = self._params()

        assert params.seat_preference == ""
        assert not params.wants_specific_seats()
        assert params.seats_wanted.is_empty()

    def test_reads_back_what_was_stored(self):
        params = self._params(seat_preference="A,D:1-15")

        assert params.wants_specific_seats()
        assert params.seats_wanted == SeatPreference(columns=("A", "D"), row_min=1, row_max=15)
