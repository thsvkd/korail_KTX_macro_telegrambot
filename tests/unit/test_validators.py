"""
Comprehensive tests for input validators.

Tests all edge cases, boundary conditions, and error handling.
"""

from datetime import datetime, timedelta

import pytest

from korail_bot.utils.validators import InputValidator


class TestPhoneNumberValidation:
    """Test phone number validation."""

    def test_valid_phone_with_hyphens(self):
        """Test valid phone number with hyphens."""
        error = InputValidator.validate_phone_number("010-1234-5678")
        assert error is None

    def test_valid_phone_011(self):
        """Test valid 011 phone number."""
        error = InputValidator.validate_phone_number("011-123-4567")
        assert error is None

    def test_valid_phone_without_hyphens(self):
        """Hyphens are optional; the number is normalized instead."""
        error = InputValidator.validate_phone_number("01012345678")
        assert error is None, error
        assert InputValidator.normalize_phone_number("01012345678") == "010-1234-5678"

    def test_invalid_phone_short(self):
        """Test phone number that's too short."""
        error = InputValidator.validate_phone_number("010-123-456")
        assert error is not None

    def test_invalid_phone_wrong_prefix(self):
        """Test phone number with wrong prefix."""
        error = InputValidator.validate_phone_number("020-1234-5678")
        assert error is not None

    def test_invalid_phone_empty(self):
        """Test empty phone number."""
        error = InputValidator.validate_phone_number("")
        assert error is not None

    def test_invalid_phone_with_letters(self):
        """Test phone number with letters."""
        error = InputValidator.validate_phone_number("010-abcd-5678")
        assert error is not None


class TestDateValidation:
    """Test date validation."""

    def test_valid_future_date(self):
        """Test valid future date."""
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")
        error = InputValidator.validate_date(future_date)
        assert error is None

    def test_valid_today(self):
        """Test today's date is valid."""
        today = datetime.now().strftime("%Y%m%d")
        error = InputValidator.validate_date(today)
        assert error is None

    def test_invalid_past_date(self):
        """Test past date is invalid."""
        past_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        error = InputValidator.validate_date(past_date)
        assert error is not None
        assert "과거" in error or "오늘" in error

    def test_invalid_date_format(self):
        """Test invalid date format."""
        error = InputValidator.validate_date("2023-01-01")
        assert error is not None

    def test_invalid_date_length(self):
        """Test date with wrong length."""
        error = InputValidator.validate_date("202301")
        assert error is not None

    def test_invalid_date_non_numeric(self):
        """Test date with non-numeric characters."""
        error = InputValidator.validate_date("abcd0101")
        assert error is not None

    def test_invalid_date_month_boundary(self):
        """Test invalid month (13)."""
        error = InputValidator.validate_date("20231301")
        assert error is not None

    def test_invalid_date_day_boundary(self):
        """Test invalid day (32)."""
        error = InputValidator.validate_date("20230132")
        assert error is not None

    def test_valid_leap_year(self):
        """Test leap year date (Feb 29 in leap year)."""
        # Find next leap year
        year = datetime.now().year
        while year % 4 != 0 or (year % 100 == 0 and year % 400 != 0):
            year += 1

        if year > datetime.now().year:
            date = f"{year}0229"
            error = InputValidator.validate_date(date)
            # Feb 29 of a leap year is a real date. It may still be refused for
            # being further ahead than booking opens, but never for not
            # existing - which is the thing this test is about.
            assert error is None or "유효하지 않은 날짜" not in error

    def test_invalid_non_leap_year_feb29(self):
        """Test Feb 29 in non-leap year."""
        # 2023 is not a leap year
        if datetime.now().year <= 2023:
            error = InputValidator.validate_date("20230229")
            assert error is not None


class TestTimeValidation:
    """Test time validation."""

    def test_valid_morning_time(self):
        """Test valid morning time."""
        error = InputValidator.validate_time("0900")
        assert error is None

    def test_valid_afternoon_time(self):
        """Test valid afternoon time."""
        error = InputValidator.validate_time("1430")
        assert error is None

    def test_valid_evening_time(self):
        """Test valid evening time."""
        error = InputValidator.validate_time("2159")
        assert error is None

    def test_valid_midnight(self):
        """Test midnight (0000)."""
        error = InputValidator.validate_time("0000")
        assert error is None

    def test_valid_last_minute(self):
        """Test last minute of day (2359)."""
        error = InputValidator.validate_time("2359")
        assert error is None

    def test_invalid_hour_24(self):
        """Test invalid hour (24)."""
        error = InputValidator.validate_time("2400")
        assert error is not None

    def test_invalid_hour_25(self):
        """Test invalid hour (25)."""
        error = InputValidator.validate_time("2560")
        assert error is not None

    def test_invalid_minute_60(self):
        """Test invalid minute (60)."""
        error = InputValidator.validate_time("1060")
        assert error is not None

    def test_invalid_minute_99(self):
        """Test invalid minute (99)."""
        error = InputValidator.validate_time("1099")
        assert error is not None

    def test_invalid_time_short(self):
        """Test time that's too short."""
        error = InputValidator.validate_time("123")
        assert error is not None

    def test_invalid_time_long(self):
        """Test time that's too long."""
        error = InputValidator.validate_time("12345")
        assert error is not None

    def test_invalid_time_non_numeric(self):
        """Test time with non-numeric characters."""
        error = InputValidator.validate_time("12ab")
        assert error is not None


class TestStationNameValidation:
    """Test station name validation."""

    def test_valid_station_seoul(self):
        """Test valid station: Seoul."""
        error = InputValidator.validate_station_name("서울")
        assert error is None

    def test_valid_station_busan(self):
        """Test valid station: Busan."""
        error = InputValidator.validate_station_name("부산")
        assert error is None

    def test_valid_station_dongdaegu(self):
        """Test valid station: Dongdaegu."""
        error = InputValidator.validate_station_name("동대구")
        assert error is None

    def test_invalid_station_empty(self):
        """Test empty station name."""
        error = InputValidator.validate_station_name("")
        assert error is not None

    def test_invalid_station_too_short(self):
        """Test station name that's too short."""
        error = InputValidator.validate_station_name("서")
        assert error is not None

    def test_invalid_station_too_long(self):
        """Test station name that's too long."""
        error = InputValidator.validate_station_name("가나다라마바사아자차카")
        assert error is not None

    def test_invalid_station_with_suffix(self):
        """Test station name with '역' suffix."""
        error = InputValidator.validate_station_name("서울역")
        assert error is not None
        assert "역" in error

    def test_invalid_station_nonexistent(self):
        """Test nonexistent station name."""
        error = InputValidator.validate_station_name("가짜스테이션")
        assert error is not None
        # Either "존재하지" (not found) or some error should be present
        assert error is not None and len(error) > 0

    def test_invalid_station_whitespace(self):
        """Test station name with only whitespace."""
        error = InputValidator.validate_station_name("   ")
        assert error is not None

    @pytest.mark.parametrize(
        "station", ["울산(통도사)", "진부(오대산)", "판교(경기)", "판교(충남)"]
    )
    def test_valid_station_with_brackets(self, station):
        """
        Korail disambiguates four of its own stations with brackets.

        These were refused as containing special characters, which put the
        stations out of reach entirely and told the user their spelling was
        wrong when it matched the station list exactly.
        """
        error = InputValidator.validate_station_name(station)
        assert error is None, error

    @pytest.mark.parametrize("station", ["서울;drop", "부산<script>", '대전"', "서울&부산"])
    def test_invalid_station_special_characters_still_refused(self, station):
        """Allowing brackets must not have opened the check up generally."""
        error = InputValidator.validate_station_name(station)
        assert error is not None
        assert "특수문자" in error


class TestYesNoValidation:
    """Test yes/no validation."""

    def test_valid_yes_uppercase(self):
        """Test 'Y' for yes."""
        result, _ = InputValidator.validate_yes_no("Y")
        assert result is True

    def test_valid_yes_lowercase(self):
        """Test 'y' for yes."""
        result, _ = InputValidator.validate_yes_no("y")
        assert result is True

    def test_valid_no_uppercase(self):
        """Test 'N' for no."""
        result, _ = InputValidator.validate_yes_no("N")
        assert result is False

    def test_valid_no_lowercase(self):
        """Test 'n' for no."""
        result, _ = InputValidator.validate_yes_no("n")
        assert result is False

    def test_invalid_maybe(self):
        """Test invalid input."""
        result, error = InputValidator.validate_yes_no("maybe")
        assert result is None
        assert error is not None


class TestChoiceValidation:
    """Test choice validation."""

    def test_valid_train_type_ktx(self):
        """Test valid train type choice: KTX."""
        error = InputValidator.validate_train_type_choice("1")
        assert error is None

    def test_valid_train_type_all(self):
        """Test valid train type choice: ALL."""
        error = InputValidator.validate_train_type_choice("2")
        assert error is None

    def test_invalid_train_type_zero(self):
        """Test invalid train type choice: 0."""
        error = InputValidator.validate_train_type_choice("0")
        assert error is not None

    def test_invalid_train_type_three(self):
        """Test invalid train type choice: 3."""
        error = InputValidator.validate_train_type_choice("3")
        assert error is not None

    def test_valid_special_option_general_first(self):
        """Test valid special option: GENERAL_FIRST."""
        error = InputValidator.validate_special_option_choice("1")
        assert error is None

    def test_valid_special_option_all(self):
        """Test all special options (1-4) are valid."""
        for choice in ["1", "2", "3", "4"]:
            error = InputValidator.validate_special_option_choice(choice)
            assert error is None

    def test_invalid_special_option_zero(self):
        """Test invalid special option: 0."""
        error = InputValidator.validate_special_option_choice("0")
        assert error is not None

    def test_invalid_special_option_five(self):
        """Test invalid special option: 5."""
        error = InputValidator.validate_special_option_choice("5")
        assert error is not None


class TestPassengerCountValidation:
    """Test passenger count validation."""

    def test_valid_single_passenger(self):
        """Test valid single passenger."""
        error = InputValidator.validate_passenger_count("1")
        assert error is None

    def test_valid_multiple_passengers(self):
        """Test valid multiple passengers."""
        error = InputValidator.validate_passenger_count("5")
        assert error is None

    def test_valid_max_passengers(self):
        """Test maximum passengers (9)."""
        error = InputValidator.validate_passenger_count("9")
        assert error is None

    def test_invalid_zero_passengers(self):
        """Test zero passengers is invalid."""
        error = InputValidator.validate_passenger_count("0")
        assert error is not None
        assert "최소 1명" in error

    def test_invalid_too_many_passengers(self):
        """Test too many passengers (>9)."""
        error = InputValidator.validate_passenger_count("10")
        assert error is not None
        assert "최대 9명" in error

    def test_invalid_negative_passengers(self):
        """Test negative passengers."""
        # Note: Since we check for isdigit(), "-1" will be caught as non-digit
        error = InputValidator.validate_passenger_count("-1")
        assert error is not None

    def test_invalid_non_numeric(self):
        """Test non-numeric passenger count."""
        error = InputValidator.validate_passenger_count("abc")
        assert error is not None
        assert "숫자" in error

    def test_invalid_empty(self):
        """Test empty passenger count."""
        error = InputValidator.validate_passenger_count("")
        assert error is not None


class TestSeatStrategyValidation:
    """Test seat strategy validation."""

    def test_valid_consecutive_seats(self):
        """Test valid consecutive seat strategy."""
        error = InputValidator.validate_seat_strategy_choice("1")
        assert error is None

    def test_valid_random_seats(self):
        """Test valid random seat strategy."""
        error = InputValidator.validate_seat_strategy_choice("2")
        assert error is None

    def test_invalid_zero(self):
        """Test invalid choice: 0."""
        error = InputValidator.validate_seat_strategy_choice("0")
        assert error is not None

    def test_invalid_three(self):
        """Test invalid choice: 3."""
        error = InputValidator.validate_seat_strategy_choice("3")
        assert error is not None

    def test_invalid_non_digit(self):
        """Test non-digit choice."""
        error = InputValidator.validate_seat_strategy_choice("a")
        assert error is not None


class TestPasswordValidation:
    """Test password validation."""

    def test_valid_simple_password(self):
        """Test valid simple password."""
        error = InputValidator.validate_password("1234")
        assert error is None

    def test_valid_complex_password(self):
        """Test valid complex password."""
        error = InputValidator.validate_password("MyP@ssw0rd!")
        assert error is None

    def test_invalid_too_short(self):
        """Test password that's too short."""
        error = InputValidator.validate_password("123")
        assert error is not None
        assert "짧습니다" in error

    def test_invalid_too_long(self):
        """Test password that's too long."""
        long_pw = "a" * 51
        error = InputValidator.validate_password(long_pw)
        assert error is not None
        assert "깁니다" in error

    def test_invalid_empty(self):
        """Test empty password."""
        error = InputValidator.validate_password("")
        assert error is not None

    def test_script_like_password_is_accepted(self):
        """
        The password is encrypted and posted to Korail's login endpoint; it
        reaches neither SQL nor a rendered page. Screening it here blocked
        real passwords (anything containing 'drop') without preventing any
        injection.
        """
        error = InputValidator.validate_password("<script>alert('xss')</script>")
        assert error is None, error

    def test_sql_like_password_is_accepted(self):
        """See above: a password is a secret to forward, not a query."""
        error = InputValidator.validate_password("password'; DROP TABLE users--")
        assert error is None, error


class TestPhoneNumberEnhancedValidation:
    """Test enhanced phone number validation."""

    def test_valid_with_whitespace(self):
        """Test valid phone with leading/trailing whitespace."""
        error = InputValidator.validate_phone_number("  010-1234-5678  ")
        assert error is None

    def test_invalid_sql_injection(self):
        """Test phone with SQL injection attempt."""
        error = InputValidator.validate_phone_number("010-1234-5678; DROP TABLE users")
        assert error is not None

    def test_invalid_script_injection(self):
        """Test phone with script injection attempt."""
        error = InputValidator.validate_phone_number("<script>alert('xss')</script>")
        assert error is not None


class TestDateEnhancedValidation:
    """Test enhanced date validation."""

    def test_invalid_too_far_future(self):
        """Test date that's too far in the future."""
        far_future = (datetime.now() + timedelta(days=400)).strftime("%Y%m%d")
        error = InputValidator.validate_date(far_future)
        assert error is not None
        assert "기간" in error or "초과" in error

    def test_valid_with_whitespace(self):
        """Test valid date with whitespace."""
        future_date = (datetime.now() + timedelta(days=7)).strftime("%Y%m%d")
        error = InputValidator.validate_date(f"  {future_date}  ")
        assert error is None

    def test_invalid_year_too_old(self):
        """Test date with year too old."""
        error = InputValidator.validate_date("19990101")
        assert error is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
