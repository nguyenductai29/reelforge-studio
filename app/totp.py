"""Time-based one-time passwords (RFC 6238; Phase 22) and 2FA recovery codes.

Authenticator apps (Google Authenticator, 1Password, Authy…) use HMAC-SHA1, 6 digits and
30-second steps; so does this module. ``verify`` accepts the previous, current and next
step (clock drift) and rejects any step at or before ``last_step``, so one code cannot be
replayed, even within its 30 seconds.

Recovery codes are ten random ``XXXX-XXXX`` codes from an alphabet without look-alike
characters. Only a SHA-256 digest bound to the user is stored; each code works once.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
ISSUER = "ReelForge Studio"
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RECOVERY_COUNT = 10


def new_secret() -> str:
    """A 160-bit secret in base32 (what authenticator apps expect), without padding."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    cleaned = secret.strip().replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def step_at(at: float | None = None) -> int:
    return int((time.time() if at is None else at) // STEP_SECONDS)


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code or "") if ch.isdigit())


def verify(secret: str, code: str, *, at: float | None = None, last_step: int | None = None,
           window: int = 1) -> int | None:
    """The step the code belongs to, or None (wrong, malformed or already used)."""
    digits = normalize(code)
    if len(digits) != DIGITS:
        return None
    current = step_at(at)
    for step in range(current - window, current + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), digits):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    label = quote(f"{issuer}:{account}", safe="@:")
    query = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS,
                       "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{query}"


def qr_data_uri(uri: str) -> str:
    """The provisioning URI as an SVG QR code (a data: URI the browser shows as an image)."""
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#111111", light="#ffffff")


def new_recovery_codes(count: int = RECOVERY_COUNT) -> list[str]:
    codes = []
    while len(codes) < count:
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(8))
        code = f"{raw[:4]}-{raw[4:]}"
        if code not in codes:
            codes.append(code)
    return codes


def normalize_recovery(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch in RECOVERY_ALPHABET)


def recovery_digest(user_id: str, code: str) -> str:
    return hashlib.sha256(f"{user_id}:{normalize_recovery(code)}".encode("utf-8")).hexdigest()


def is_totp_code(code: str) -> bool:
    """Six digits (spaces allowed): an authenticator code. Anything else is tried as a recovery code."""
    text = str(code or "").replace(" ", "")
    return len(text) == DIGITS and text.isdigit()
