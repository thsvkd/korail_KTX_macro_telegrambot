"""Input validation utilities."""

import re
from datetime import datetime


class InputValidator:
    """Validator for user inputs in the reservation flow."""

    @staticmethod
    def normalize_phone_number(phone: str) -> str | None:
        """
        Put a Korean mobile number into the hyphenated form Korail expects.

        People type their number every way imaginable - '01012345678',
        '010 1234 5678', '010.1234.5678'. Korail wants '010-1234-5678', so
        the input is reduced to digits and rebuilt rather than rejected.

        Args:
            phone: Phone number as typed

        Returns:
            Normalized number, or None when it is not a mobile number
        """
        if not phone:
            return None

        # Only separators may be dropped. Stripping anything else would turn
        # a typo into a different, valid-looking number: '010-1234-567a'
        # would silently become '010-123-4567'.
        if not re.fullmatch(r"[0-9\s.\-()+]+", phone.strip()):
            return None

        digits = "".join(character for character in phone if character.isdigit())

        if not digits.startswith("01"):
            return None

        # 010-1234-5678 (11 digits) and the older 011-123-4567 (10 digits)
        if len(digits) == 11:
            return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
        if len(digits) == 10:
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"

        return None

    @staticmethod
    def validate_phone_number(phone: str) -> tuple[bool, str | None]:
        """
        Validate a Korean mobile number, in whatever shape it was typed.

        Hyphens are optional: the number is normalized first and only the
        result has to look like a mobile number. Rejecting '01012345678' for
        punctuation would be pedantry, not validation.

        Args:
            phone: Phone number string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not phone or not phone.strip():
            return False, "전화번호를 입력해주세요."

        phone = phone.strip()

        # Anything that is not a digit or common separator is not a typo.
        if not re.fullmatch(r"[0-9\s.\-()+]+", phone):
            return False, "전화번호는 숫자로만 입력해주세요. (예: 010-1234-5678)"

        if InputValidator.normalize_phone_number(phone) is None:
            return False, "올바른 전화번호 형식이 아닙니다. (예: 010-1234-5678)"

        return True, None

    @staticmethod
    def validate_date(date_str: str) -> tuple[bool, str | None]:
        """
        Validate date in YYYYMMDD format with enhanced validation.

        Args:
            date_str: Date string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not date_str:
            return False, "날짜를 입력해주세요."

        # Trim whitespace
        date_str = date_str.strip()

        if not date_str:
            return False, "날짜를 입력해주세요."

        # Check for non-digit characters
        if not date_str.isdigit():
            return False, "날짜는 숫자만 입력해주세요. (예: 20250101)"

        # Check length
        if len(date_str) != 8:
            return False, "날짜는 8자리로 입력해주세요. (예: 20250101)"

        # Check if date is valid
        try:
            year = int(date_str[:4])
            month = int(date_str[4:6])
            day = int(date_str[6:8])

            # Validate year range (reasonable range: 2020-2100)
            if year < 2020 or year > 2100:
                return False, f"연도가 유효하지 않습니다. (입력: {year}년)"

            # Validate month
            if month < 1 or month > 12:
                return False, f"월이 유효하지 않습니다. (입력: {month}월)"

            # Validate day
            if day < 1 or day > 31:
                return False, f"일이 유효하지 않습니다. (입력: {day}일)"

            # Parse date to check if it's valid (e.g., Feb 30 would fail)
            datetime(year, month, day)

        except ValueError:
            return False, "유효하지 않은 날짜입니다. (예: 2월 30일은 존재하지 않습니다)"

        # Check if date is not in the past
        today = datetime.today().strftime("%Y%m%d")
        if date_str < today:
            return False, "과거 날짜는 선택할 수 없습니다."

        # Check if date is too far in the future (e.g., more than 1 year ahead)
        from datetime import timedelta

        max_future_date = (datetime.today() + timedelta(days=365)).strftime("%Y%m%d")
        if date_str > max_future_date:
            return False, "예매 가능한 기간을 초과했습니다. (최대 1년 이내)"

        return True, None

    @staticmethod
    def validate_time(time_str: str) -> tuple[bool, str | None]:
        """
        Validate time in HHMM format with enhanced validation.

        Args:
            time_str: Time string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not time_str:
            return False, "시간을 입력해주세요."

        # Trim whitespace
        time_str = time_str.strip()

        if not time_str:
            return False, "시간을 입력해주세요."

        # Check for non-digit characters
        if not time_str.isdigit():
            return False, "시간은 숫자만 입력해주세요. (예: 1430은 14시 30분)"

        # Check length
        if len(time_str) != 4:
            return False, "시간은 4자리로 입력해주세요. (예: 1430은 14시 30분)"

        try:
            hours = int(time_str[:2])
            minutes = int(time_str[2:4])
        except ValueError:
            return False, "시간 형식이 올바르지 않습니다. (예: 1430)"

        # Validate hours
        if hours < 0 or hours > 23:
            return False, f"시간은 00-23 사이여야 합니다. (입력: {hours}시)"

        # Validate minutes
        if minutes < 0 or minutes > 59:
            return False, f"분은 00-59 사이여야 합니다. (입력: {minutes}분)"

        return True, None

    @staticmethod
    def validate_station_name(station: str, operator=None) -> tuple[bool, str | None]:
        """
        Validate a station name against the railway that would stop there.

        Args:
            station: Station name
            operator: Which railway the search is against. None means Korail,
                      which is what every caller meant when there was only one.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Import here to avoid circular dependency
        from korail_bot.models.operator import Operator
        from korail_bot.utils.station_codes import (
            format_station_suggestions,
            get_similar_stations,
            is_valid_station,
        )

        operator = Operator.parse(operator)

        if not station:
            return False, "역 이름을 입력해주세요."

        # Trim whitespace
        station = station.strip()

        if not station:
            return False, "역 이름을 입력해주세요."

        # Check for '역' suffix
        if "역" in station:
            return False, "'역'을 제외한 이름을 입력해주세요. (예: 광명)"

        # Check minimum length
        if len(station) < 2:
            return False, "역 이름이 너무 짧습니다. 최소 2자 이상 입력해주세요."

        # Check maximum length (reasonable limit)
        if len(station) > 10:
            return False, "역 이름이 너무 깁니다. 올바른 역 이름을 입력해주세요."

        # Check for invalid characters
        if not station.replace(" ", "").replace("-", "").isalnum():
            # Allow Korean, numbers, spaces, hyphens and brackets.
            #
            # Brackets are not decoration: Korail disambiguates four of its
            # own stations with them - 울산(통도사), 진부(오대산), 판교(경기),
            # 판교(충남). Refusing them here rejected the name the station
            # list itself holds, so those four could not be reached at all,
            # and the error blamed the user for a typo they had not made.
            if not all(
                c.isalnum()
                or c
                in [
                    " ",
                    "-",
                    "(",
                    ")",
                    "ㄱ",
                    "ㄴ",
                    "ㄷ",
                    "ㄹ",
                    "ㅁ",
                    "ㅂ",
                    "ㅅ",
                    "ㅇ",
                    "ㅈ",
                    "ㅊ",
                    "ㅋ",
                    "ㅌ",
                    "ㅍ",
                    "ㅎ",
                ]
                for c in station
            ):
                return False, "역 이름에 특수문자를 사용할 수 없습니다."

        # Check against the station list of the railway being booked.
        #
        # SR publishes its whole list with its client - 30-odd stations - so
        # the answer is definite and the suggestions are drawn from it. Korail
        # runs hundreds and its list is fetched and cached, which is what
        # station_codes is for.
        serves = operator.serves(station)
        if serves is False:
            from korail_bot.models.operator import SRT_MAJOR_STATIONS

            offered = ", ".join(SRT_MAJOR_STATIONS[:6])
            return False, (
                f"'{station}'은(는) {operator.display_name}이(가) 서지 않는 역입니다.\n"
                f"예: {offered} 등"
            )

        if serves is None and not is_valid_station(station):
            # Get similar stations for suggestion
            similar = get_similar_stations(station)
            suggestion_text = format_station_suggestions(similar)

            error_msg = f"'{station}'은(는) 존재하지 않는 역입니다.{suggestion_text}"
            return False, error_msg

        return True, None

    @staticmethod
    def validate_yes_no(answer: str) -> tuple[bool | None, str | None]:
        """
        Validate yes/no answer with enhanced security.

        Three answers, not two: yes, no, and "that was not an answer". The
        third one has to stay distinct from no, because the caller re-asks on
        it rather than proceeding as if the user had declined.

        Args:
            answer: User's answer

        Returns:
            Tuple of (True, None), (False, None), or (None, error_message)
        """
        if not answer:
            return None, "Y/예 또는 N/아니오를 입력해주세요."

        # Trim and convert to uppercase
        answer = answer.strip().upper()

        if not answer:
            return None, "Y/예 또는 N/아니오를 입력해주세요."

        # Check length (prevent long inputs)
        if len(answer) > 10:
            return None, "입력이 너무 깁니다. Y/예 또는 N/아니오를 입력해주세요."

        # Valid positive responses
        if answer in ["Y", "예", "YES", "네", "ㅇ"]:
            return True, None
        # Valid negative responses
        elif answer in ["N", "아니오", "NO", "아니요", "ㄴ"]:
            return False, None
        else:
            return None, "Y/예 또는 N/아니오를 입력해주세요."

    @staticmethod
    def validate_operator_choice(choice: str) -> tuple[bool, str | None]:
        """
        Validate which railway was chosen.

        Deliberately strict, unlike Operator.parse: that one is reading a
        stored record and a wrong guess there strands a search someone is
        waiting on, so it settles for Korail. This one is reading an answer to
        a question just asked, and answering something unrecognisable with
        "right, Korail then" would book the wrong railway without a word.

        Args:
            choice: What the user pressed or typed

        Returns:
            Tuple of (is_valid, error_message)
        """
        from korail_bot.models.operator import Operator

        if not (choice or "").strip():
            return False, "철도를 선택해주세요. (코레일 또는 SRT)"

        if Operator.from_answer(choice) is None:
            return False, "코레일 또는 SRT 중에서 선택해주세요."

        return True, None

    @staticmethod
    def validate_train_type_choice(choice: str) -> tuple[bool, str | None]:
        """
        Validate train type choice (1 or 2) with enhanced validation.

        Args:
            choice: User's choice

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not choice:
            return False, "열차 종류를 선택해주세요. (1: KTX만, 2: 전체)"

        # Trim whitespace
        choice = choice.strip()

        if not choice:
            return False, "열차 종류를 선택해주세요. (1: KTX만, 2: 전체)"

        # Check if it's a digit
        if not choice.isdigit():
            return False, "숫자를 입력해주세요. (1 또는 2)"

        # Validate choice
        if choice not in ["1", "2"]:
            return False, "1 또는 2를 입력해주세요. (1: KTX만, 2: 전체)"

        return True, None

    @staticmethod
    def validate_special_option_choice(choice: str) -> tuple[bool, str | None]:
        """
        Validate special seat option choice (1, 2, 3, or 4) with enhanced validation.

        Args:
            choice: User's choice

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not choice:
            return False, "좌석 옵션을 선택해주세요. (1~4)"

        # Trim whitespace
        choice = choice.strip()

        if not choice:
            return False, "좌석 옵션을 선택해주세요. (1~4)"

        # Check if it's a digit
        if not choice.isdigit():
            return False, "숫자를 입력해주세요. (1, 2, 3, 또는 4)"

        # Validate choice
        if choice not in ["1", "2", "3", "4"]:
            return False, "1, 2, 3, 4 중 하나를 선택해주세요."

        return True, None

    @staticmethod
    def validate_passenger_count(count_str: str) -> tuple[bool, str | None]:
        """
        Validate passenger count with enhanced validation.

        Args:
            count_str: Passenger count as string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not count_str:
            return False, "승객 수를 입력해주세요."

        # Trim whitespace
        count_str = count_str.strip()

        if not count_str:
            return False, "승객 수를 입력해주세요."

        # Check if it's a digit
        if not count_str.isdigit():
            return False, "승객 수는 숫자만 입력해주세요. (1~9)"

        try:
            count = int(count_str)
        except ValueError:
            return False, "유효한 숫자를 입력해주세요."

        # Validate range
        if count < 1:
            return False, "승객 수는 최소 1명 이상이어야 합니다."

        if count > 9:
            return False, "승객 수는 최대 9명까지 가능합니다."

        return True, None

    @staticmethod
    def validate_seat_strategy_choice(choice: str) -> tuple[bool, str | None]:
        """
        Validate seat strategy choice (1 or 2).

        Args:
            choice: User's choice

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not choice:
            return False, "좌석 배치 방식을 선택해주세요. (1: 연속 좌석, 2: 랜덤 배치)"

        # Trim whitespace
        choice = choice.strip()

        if not choice:
            return False, "좌석 배치 방식을 선택해주세요. (1: 연속 좌석, 2: 랜덤 배치)"

        # Check if it's a digit
        if not choice.isdigit():
            return False, "숫자를 입력해주세요. (1 또는 2)"

        # Validate choice
        if choice not in ["1", "2"]:
            return False, "1 또는 2를 입력해주세요. (1: 연속 좌석, 2: 랜덤 배치)"

        return True, None

    @staticmethod
    def validate_password(password: str) -> tuple[bool, str | None]:
        """
        Validate password input with basic security checks.

        Args:
            password: Password string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not password:
            return False, "비밀번호를 입력해주세요."

        # Check minimum length
        if len(password) < 4:
            return False, "비밀번호가 너무 짧습니다."

        # Check maximum length (reasonable limit)
        if len(password) > 50:
            return False, "비밀번호가 너무 깁니다."

        # No pattern blocklist here on purpose. This value is only ever
        # encrypted and posted to Korail's login endpoint - it reaches neither
        # SQL nor a page - so screening it for 'SELECT' or 'DROP' cannot
        # prevent an injection. It only rejects real passwords: anything
        # containing 'drop' ("Raindrop2024") was refused as malicious.
        if any(character in password for character in ("\n", "\r", "\t")):
            return False, "비밀번호에 줄바꿈이나 탭을 포함할 수 없습니다."

        return True, None
