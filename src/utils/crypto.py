"""
Encryption helpers for secrets that have to be persisted.

Korail credentials are supplied by users over Telegram and must survive a
few conversation steps in Redis. They are stored encrypted so that read
access to Redis (a dump, a snapshot, an exposed port) does not hand over
plaintext passwords.

The `cryptography` package ships with the application (pinned in
requirements.txt, pulled in via pyopenssl in Pipfile.lock).
"""
import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

# Marks values produced by this module. Anything without the prefix is
# treated as unreadable rather than as a plaintext fallback.
_PREFIX = "v1:"

# Fixed salt: the key must be reproducible across restarts, and SESSION_SECRET
# is expected to be high-entropy key material rather than a human password.
_KDF_SALT = b"korailbot.session.v1"
_KDF_ITERATIONS = 200_000


class SecretBox:
    """Symmetric encryption for short secrets."""

    def __init__(self, secret: Optional[str] = None):
        """
        Build a secret box.

        Args:
            secret: Key material. Defaults to settings.SESSION_SECRET.
                    When absent, an ephemeral key is generated, which means
                    stored secrets cannot be read back after a restart.
        """
        secret = secret if secret is not None else settings.SESSION_SECRET

        if secret:
            self._ephemeral = False
            key = hashlib.pbkdf2_hmac(
                'sha256', secret.encode('utf-8'), _KDF_SALT, _KDF_ITERATIONS, dklen=32
            )
            self._fernet = Fernet(base64.urlsafe_b64encode(key))
        else:
            self._ephemeral = True
            self._fernet = Fernet(Fernet.generate_key())
            logger.warning(
                "SESSION_SECRET is not set - using an ephemeral encryption key. "
                "Stored credentials will not be readable after a restart."
            )

    @property
    def is_ephemeral(self) -> bool:
        """True when the key is not derived from configured key material."""
        return self._ephemeral

    def encrypt(self, plaintext: Optional[str]) -> Optional[str]:
        """
        Encrypt a secret for storage.

        Args:
            plaintext: Value to protect. Empty values are passed through.

        Returns:
            Prefixed ciphertext, or the original value when there is nothing
            to encrypt.
        """
        if not plaintext:
            return plaintext

        token = self._fernet.encrypt(plaintext.encode('utf-8')).decode('ascii')
        return f"{_PREFIX}{token}"

    def decrypt(self, ciphertext: Optional[str]) -> Optional[str]:
        """
        Decrypt a stored secret.

        Args:
            ciphertext: Value produced by encrypt().

        Returns:
            The plaintext, or None when the value cannot be read (wrong key,
            tampered payload, or a legacy plaintext record). Callers treat
            None as "no credentials" and ask the user to enter them again.
        """
        if not ciphertext:
            return None

        if not ciphertext.startswith(_PREFIX):
            logger.warning(
                "Encountered a stored secret without an encryption marker - "
                "discarding it and requiring re-entry."
            )
            return None

        try:
            return self._fernet.decrypt(
                ciphertext[len(_PREFIX):].encode('ascii')
            ).decode('utf-8')
        except (InvalidToken, ValueError):
            logger.warning(
                "Stored secret could not be decrypted (key rotated or data "
                "tampered with) - requiring re-entry."
            )
            return None


_secret_box: Optional[SecretBox] = None


def get_secret_box() -> SecretBox:
    """Get the process-wide secret box."""
    global _secret_box
    if _secret_box is None:
        _secret_box = SecretBox()
    return _secret_box
