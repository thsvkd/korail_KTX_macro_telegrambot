"""
Unit tests for the input rules users actually hit.

These cover the phone number people type in whatever shape they like, and
the password field that used to reject perfectly good passwords.
"""
from unittest.mock import patch

import pytest

from config.settings import Settings, settings
from utils.validators import InputValidator


class TestPhoneNormalization:
    """Hyphens are punctuation, not part of the number."""

    @pytest.mark.parametrize("typed", [
        "010-1234-5678",
        "01012345678",
        "010 1234 5678",
        "010.1234.5678",
        "  010-1234-5678  ",
    ])
    def test_every_shape_reaches_the_same_number(self, typed):
        assert InputValidator.normalize_phone_number(typed) == "010-1234-5678"

    def test_older_ten_digit_numbers_keep_their_shape(self):
        assert InputValidator.normalize_phone_number("0111234567") == "011-123-4567"

    @pytest.mark.parametrize("typed", [
        "010-1234-5678",
        "01012345678",
        "010 1234 5678",
    ])
    def test_validation_accepts_them_all(self, typed):
        is_valid, error = InputValidator.validate_phone_number(typed)

        assert is_valid is True, error

    @pytest.mark.parametrize("typed", [
        "",
        "0101234",           # too short
        "010123456789",      # too long
        "021234567",         # not a mobile number
        "abcdefghijk",
        "010-1234-567a",
    ])
    def test_rubbish_is_still_refused(self, typed):
        is_valid, _ = InputValidator.validate_phone_number(typed)

        assert is_valid is False
        assert InputValidator.normalize_phone_number(typed) is None

    def test_error_message_shows_the_expected_shape(self):
        _, error = InputValidator.validate_phone_number("12345")

        assert "010-1234-5678" in error


class TestAllowList:
    """The list should not care how a number was written down."""

    def test_hyphenless_input_matches_a_hyphenated_entry(self):
        with patch.object(Settings, 'ALLOW_LIST', ["010-1234-5678"]):
            assert settings.is_user_allowed("01012345678") is True

    def test_hyphenated_input_matches_a_hyphenless_entry(self):
        with patch.object(Settings, 'ALLOW_LIST', ["01012345678"]):
            assert settings.is_user_allowed("010-1234-5678") is True

    def test_a_different_number_is_still_refused(self):
        with patch.object(Settings, 'ALLOW_LIST', ["010-1234-5678"]):
            assert settings.is_user_allowed("010-9999-8888") is False

    def test_an_empty_list_allows_everyone(self):
        with patch.object(Settings, 'ALLOW_LIST', []):
            assert settings.is_user_allowed("010-1234-5678") is True

    def test_blank_entries_do_not_allow_everyone(self):
        with patch.object(Settings, 'ALLOW_LIST', ["010-1234-5678", " "]):
            assert settings.is_user_allowed("010-0000-0000") is False


class TestPasswordValidation:
    """
    The password is encrypted and posted to Korail. It never reaches SQL or a
    page, so screening it for injection payloads only rejected real passwords.
    """

    @pytest.mark.parametrize("password", [
        "Raindrop2024",      # contains 'drop'
        "MySelection!1",     # contains 'select'
        "insert-coin-99",    # contains 'insert'
        "p@ssw0rd<script>",  # not our problem to sanitise here
        "한글비밀번호",
        "a" * 50,
    ])
    def test_real_passwords_are_accepted(self, password):
        is_valid, error = InputValidator.validate_password(password)

        assert is_valid is True, f"{password!r} was refused: {error}"

    @pytest.mark.parametrize("password", ["", "abc", "a" * 51])
    def test_length_bounds_still_apply(self, password):
        is_valid, _ = InputValidator.validate_password(password)

        assert is_valid is False

    @pytest.mark.parametrize("password", ["with\nnewline", "with\ttab"])
    def test_control_characters_are_refused(self, password):
        is_valid, _ = InputValidator.validate_password(password)

        assert is_valid is False
