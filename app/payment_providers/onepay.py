"""OnePAY card payments (domestic ATM and international Visa/Mastercard/JCB cards) through the OnePAY gateway.

Contract (OnePAY merchant integration, ``vpc_Version`` 2):

* **Checkout:** the buyer is redirected to ``ONEPAY_PAYMENT_URL`` with ``vpc_*``
  fields and ``vpc_SecureHash``. The hash is HMAC-SHA256, keyed with the
  hex-decoded merchant hash key, over the ``key=value`` pairs of every non-empty
  ``vpc_`` and ``user_`` field except the hash fields, sorted by key and joined
  by ``&``. It is sent as uppercase hex.
* **Result:** OnePAY sends the buyer back to ``vpc_ReturnURL``, and calls the
  merchant's IPN URL (registered with OnePAY) server to server, with the same
  signed fields. ``vpc_TxnResponseCode`` ``0`` means approved, and ``vpc_Amount``
  is the amount × 100.
* **QueryDR:** ``ONEPAY_QUERY_URL`` with ``vpc_Command=queryDR`` and the query
  user and password returns the signed current state of one transaction. It is
  the server-side evidence used when the buyer's browser returns.

Nothing here trusts an unsigned field: every result is checked against its
signature before it is read, and the amount is compared by the caller.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import os
import re
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

PRODUCTION_PAYMENT_URL = "https://onepay.vn/paygate/vpcpay.op"
PRODUCTION_QUERY_URL = "https://onepay.vn/msp/api/v1/vpc/invoices/queries"
HASH_FIELDS = frozenset({"vpc_SecureHash", "vpc_SecureHashType"})
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_REF = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
_HEX = re.compile(r"(?:[0-9A-Fa-f]{2})+\Z")
# Response codes: 0 approved, 99 the buyer cancelled, 300 still pending at the bank; anything else declined.
APPROVED, CANCELLED, PENDING = "0", "99", "300"


class OnePayError(Exception):
    """A configuration, signature or gateway problem with a stable ``code``; never contains the hash key."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OnePayConfig:
    merchant: str
    access_code: str
    hash_key: str = field(repr=False)
    payment_url: str = PRODUCTION_PAYMENT_URL
    query_url: str = PRODUCTION_QUERY_URL
    query_user: str = ""
    query_password: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if (not self.merchant.strip() or not self.access_code.strip() or not _HEX.fullmatch(self.hash_key or "")
                or any(urlsplit(url).scheme != "https" or not urlsplit(url).hostname
                       for url in (self.payment_url, self.query_url))):
            raise OnePayError("not_configured", "OnePAY configuration is incomplete or invalid")

    @property
    def can_query(self) -> bool:
        return bool(self.query_user and self.query_password)

    @classmethod
    def from_environment(cls) -> "OnePayConfig":
        """The legacy configuration from ``ONEPAY_*``; checkout and callbacks use ``app.payment_config``."""
        env = os.environ.get
        return cls(merchant=env("ONEPAY_MERCHANT_ID", ""), access_code=env("ONEPAY_ACCESS_CODE", ""),
                   hash_key=env("ONEPAY_HASH_KEY", "").strip(),
                   payment_url=env("ONEPAY_PAYMENT_URL", "").strip() or PRODUCTION_PAYMENT_URL,
                   query_url=env("ONEPAY_QUERY_URL", "").strip() or PRODUCTION_QUERY_URL,
                   query_user=env("ONEPAY_QUERY_USER", ""), query_password=env("ONEPAY_QUERY_PASSWORD", ""))


def configured() -> bool:
    """Whether the legacy ``ONEPAY_*`` environment holds a valid configuration.

    The configuration in use is resolved by ``app.payment_config`` (an admin-managed one first)."""
    try:
        OnePayConfig.from_environment()
    except OnePayError:
        return False
    return True


def secure_hash(params: Mapping[str, str], hash_key: str) -> str:
    """The ``vpc_SecureHash`` of ``params`` (see the module docstring)."""
    pairs = sorted((key, str(value)) for key, value in params.items()
                   if key.startswith(("vpc_", "user_")) and key not in HASH_FIELDS and str(value) != "")
    message = "&".join(f"{key}={value}" for key, value in pairs)
    return hmac.new(bytes.fromhex(hash_key), message.encode("utf-8"), hashlib.sha256).hexdigest().upper()


def verify(params: Mapping[str, str], hash_key: str) -> bool:
    given = params.get("vpc_SecureHash") or ""
    return bool(given) and hmac.compare_digest(given.upper(), secure_hash(params, hash_key))


def checkout_url(config: OnePayConfig, *, merch_txn_ref: str, amount_vnd: int, order_info: str, return_url: str,
                 client_ip: str, again_link: str, locale: str = "vn") -> str:
    """The signed gateway URL the buyer is redirected to."""
    if not _REF.fullmatch(merch_txn_ref) or not isinstance(amount_vnd, int) or amount_vnd <= 0:
        raise OnePayError("invalid_request", "Invalid OnePAY order")
    if urlsplit(return_url).scheme != "https" and urlsplit(return_url).hostname not in ("localhost", "127.0.0.1"):
        raise OnePayError("invalid_request", "Public checkout requires an HTTPS return URL")
    params = {"vpc_Version": "2", "vpc_Currency": "VND", "vpc_Command": "pay", "vpc_AccessCode": config.access_code,
              "vpc_Merchant": config.merchant, "vpc_Locale": locale if locale in ("vn", "en") else "vn",
              "vpc_ReturnURL": return_url, "vpc_MerchTxnRef": merch_txn_ref, "vpc_OrderInfo": order_info[:34],
              "vpc_Amount": str(amount_vnd * 100), "vpc_TicketNo": (client_ip or "127.0.0.1")[:45],
              "AgainLink": again_link, "Title": "ReelForge"}
    params["vpc_SecureHash"] = secure_hash(params, config.hash_key)
    return f"{config.payment_url}?{urlencode(params)}"


@dataclass(frozen=True)
class OnePayResult:
    merch_txn_ref: str
    status: str  # paid, pending, failed or cancelled
    amount_vnd: int | None
    transaction_no: str
    response_code: str


def _status(code: str) -> str:
    return "paid" if code == APPROVED else "cancelled" if code == CANCELLED else "pending" if code == PENDING \
        else "failed"


def read_result(params: Mapping[str, str], config: OnePayConfig) -> OnePayResult:
    """A signed return or IPN as a result; ``OnePayError('invalid_signature')`` when it is not OnePAY's."""
    if not verify(params, config.hash_key):
        raise OnePayError("invalid_signature", "OnePAY signature does not match")
    if params.get("vpc_Merchant") not in (None, "", config.merchant):
        raise OnePayError("invalid_signature", "OnePAY result is for another merchant")
    reference = params.get("vpc_MerchTxnRef") or ""
    if not _REF.fullmatch(reference):
        raise OnePayError("invalid_response", "OnePAY result has no order reference")
    raw_amount = params.get("vpc_Amount") or ""
    amount = int(raw_amount) // 100 if raw_amount.isdigit() and int(raw_amount) % 100 == 0 else None
    code = (params.get("vpc_TxnResponseCode") or "").strip()
    return OnePayResult(reference, _status(code), amount, (params.get("vpc_TransactionNo") or "")[:64], code[:8])


def query(config: OnePayConfig, merch_txn_ref: str, *, client: httpx.Client) -> OnePayResult:
    """The signed current state of one transaction (QueryDR); server-side evidence for a browser return."""
    if not config.can_query:
        raise OnePayError("query_not_configured", "OnePAY QueryDR credentials are not configured")
    if not _REF.fullmatch(merch_txn_ref):
        raise OnePayError("invalid_request", "Invalid OnePAY order reference")
    params = {"vpc_Command": "queryDR", "vpc_Version": "2", "vpc_MerchTxnRef": merch_txn_ref,
              "vpc_Merchant": config.merchant, "vpc_AccessCode": config.access_code, "vpc_User": config.query_user,
              "vpc_Password": config.query_password}
    params["vpc_SecureHash"] = secure_hash(params, config.hash_key)
    try:
        response = client.post(config.query_url, data=params, timeout=TIMEOUT, follow_redirects=False)
    except httpx.RequestError as exc:
        raise OnePayError("unavailable", "OnePAY could not be reached") from exc
    if response.status_code != 200:
        raise OnePayError("unavailable", f"OnePAY QueryDR returned HTTP {response.status_code}")
    answer = dict(parse_qsl(response.text.strip(), keep_blank_values=True))
    if answer.get("vpc_DRExists") == "N":
        # No transaction yet: the buyer has not paid (or has not finished paying).
        return OnePayResult(merch_txn_ref, "pending", None, "", "")
    result = read_result(answer, config)
    if result.merch_txn_ref != merch_txn_ref:
        raise OnePayError("invalid_response", "OnePAY answered for another order")
    return result
