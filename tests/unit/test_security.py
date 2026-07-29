"""
Unit tests for the credential-protection and privacy helpers.

These run without Redis or a network.
"""

import pytest

from korail_bot.utils.crypto import SecretBox
from korail_bot.utils.privacy import mask_phone, mask_phones


class TestSecretBox:
    """Encryption of secrets that get persisted."""

    def test_roundtrip(self):
        box = SecretBox("test-key-material")
        assert box.decrypt(box.encrypt("hunter2")) == "hunter2"

    def test_ciphertext_does_not_contain_plaintext(self):
        box = SecretBox("test-key-material")
        ciphertext = box.encrypt("my-korail-password")

        assert "my-korail-password" not in ciphertext
        assert ciphertext.startswith("v1:")

    def test_same_plaintext_yields_different_ciphertext(self):
        """Fernet includes a random IV, so equal passwords are not linkable."""
        box = SecretBox("test-key-material")

        assert box.encrypt("same") != box.encrypt("same")

    def test_derived_key_is_stable_across_instances(self):
        """A restart with the same SESSION_SECRET can still read stored data."""
        ciphertext = SecretBox("stable-key").encrypt("password")

        assert SecretBox("stable-key").decrypt(ciphertext) == "password"

    def test_other_key_cannot_decrypt(self):
        ciphertext = SecretBox("key-one").encrypt("password")

        assert SecretBox("key-two").decrypt(ciphertext) is None

    def test_tampered_ciphertext_is_rejected(self):
        box = SecretBox("test-key-material")
        ciphertext = box.encrypt("password")
        tampered = ciphertext[:-2] + ("AA" if not ciphertext.endswith("AA") else "BB")

        assert box.decrypt(tampered) is None

    def test_plaintext_without_marker_is_rejected(self):
        """Legacy plaintext records must not be handed back as usable."""
        box = SecretBox("test-key-material")

        assert box.decrypt("plain-old-password") is None

    def test_empty_values_pass_through(self):
        box = SecretBox("test-key-material")

        assert box.encrypt("") == ""
        assert box.encrypt(None) is None
        assert box.decrypt("") is None
        assert box.decrypt(None) is None

    def test_missing_key_material_is_ephemeral(self):
        box = SecretBox("")

        assert box.is_ephemeral is True
        # Still usable within the process lifetime.
        assert box.decrypt(box.encrypt("password")) == "password"

    def test_configured_key_material_is_not_ephemeral(self):
        assert SecretBox("configured").is_ephemeral is False


class TestMaskPhone:
    """Phone numbers are Korail IDs and must not be broadcast in full."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("010-1234-5678", "010-****-5678"),
            ("011-123-4567", "011-***-4567"),
            ("010-9876-5432", "010-****-5432"),
        ],
    )
    def test_middle_block_is_masked(self, raw, expected):
        assert mask_phone(raw) == expected

    def test_surrounding_whitespace_is_tolerated(self):
        assert mask_phone("  010-1234-5678  ") == "010-****-5678"

    def test_empty_input(self):
        assert mask_phone("") == "(알 수 없음)"
        assert mask_phone(None) == "(알 수 없음)"

    def test_non_phone_input_is_still_truncated(self):
        masked = mask_phone("some-account-id")

        assert "some-account-id" not in masked
        assert masked.startswith("so")

    def test_mask_phones_maps_every_entry(self):
        assert mask_phones(["010-1111-2222", "010-3333-4444"]) == [
            "010-****-2222",
            "010-****-4444",
        ]


class TestUserSessionReset:
    """A finished flow must not leave the password behind."""

    def test_reset_clears_password(self):
        from korail_bot.models import UserCredentials, UserSession

        session = UserSession(
            chat_id=1, credentials=UserCredentials(korail_id="010-1234-5678", korail_pw="secret")
        )

        session.reset()

        assert session.credentials.korail_pw == ""
        # The ID is kept so the flow can still show who the session belongs to.
        assert session.credentials.korail_id == "010-1234-5678"
