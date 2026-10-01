"""Password hashing (scrypt), shared by the API and the server-side recovery command (``app/account_recovery.py``).

A stored hash is ``<salt hex>:<digest hex>`` with scrypt n = 2^14, r = 8, p = 1 and a 16-byte random salt.
"""
import hashlib
import secrets

_UNKNOWN_ACCOUNT_HASH: str | None = None


def hashed_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def check_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return secrets.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def check_unknown_account(password: str) -> bool:
    """The same scrypt work as a real check, for an email without a usable account: a sign-in for an unknown or
    deactivated account takes as long as a wrong password, so the timing does not reveal which accounts exist.
    Always False."""
    global _UNKNOWN_ACCOUNT_HASH
    if _UNKNOWN_ACCOUNT_HASH is None:
        _UNKNOWN_ACCOUNT_HASH = hashed_password(secrets.token_urlsafe(24))
    check_password(password, _UNKNOWN_ACCOUNT_HASH)
    return False
