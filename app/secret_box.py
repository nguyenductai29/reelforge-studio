"""Encrypt small JSON secrets at rest (payment gateways since Phase 19, every admin-managed secret since 20).

The server holds one Fernet master key outside the database (``app/master_key.py``:
``/etc/reelforge/master.key``, or the legacy ``REELFORGE_TOKEN_ENCRYPTION_KEY``),
which also encrypts every OAuth token and upload session. This module reuses it
rather than adding a second key next to it: both would sit on the same server, be
read by the same processes and be lost or leaked together, so a second key adds an
operational burden without a security boundary.

Each purpose gets its own key derived with HKDF-SHA256 (``info`` names the
purpose), so a ciphertext made for one purpose never decrypts as another: a
payment configuration cannot be swapped into an OAuth token column, or a payOS
configuration into OnePAY's row. A missing or invalid key is a configuration
error (``key_missing``); no key is ever generated here.
"""
import base64
import json

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app import master_key

# Named in messages to operators; the key itself comes from app.master_key.
KEY_VARIABLE = "REELFORGE_MASTER_KEY_FILE (/etc/reelforge/master.key)"


class SecretBoxError(Exception):
    """``key_missing`` (no valid key on this server) or ``cannot_decrypt`` (another key, or damaged data)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _master() -> bytes:
    raw = master_key.load()
    if not raw:
        raise SecretBoxError("key_missing", "The master encryption key is missing or invalid")
    return base64.urlsafe_b64decode(raw.encode("ascii"))


def _fernet(purpose: str) -> Fernet:
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=f"reelforge:{purpose}".encode("utf-8")).derive(_master())
    return Fernet(base64.urlsafe_b64encode(derived))


def available() -> bool:
    try:
        _master()
    except SecretBoxError:
        return False
    return True


def encrypt_json(purpose: str, data: dict) -> str:
    return _fernet(purpose).encrypt(json.dumps(data, sort_keys=True).encode("utf-8")).decode("ascii")


def decrypt_json(purpose: str, ciphertext: str) -> dict:
    fernet = _fernet(purpose)
    try:
        value = json.loads(fernet.decrypt(ciphertext.encode("ascii")))
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SecretBoxError("cannot_decrypt", "Stored secret cannot be decrypted with the current key") from exc
    if not isinstance(value, dict):
        raise SecretBoxError("cannot_decrypt", "Stored secret is not a JSON object")
    return value
