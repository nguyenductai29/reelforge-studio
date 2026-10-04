"""payOS adapter (VietQR). Credentials come from ``app.payment_config``: the admin-managed, encrypted
configuration when one is saved, else the ``payos`` object of the private bootstrap file; never from SQL columns."""
from urllib.parse import urlparse
from app import payment_config


def _credentials() -> dict | None:
    return payment_config.payos_credentials(payment_config.get("payos"))


def configured() -> bool:
    """Whether payOS credentials are usable: enough for webhooks and status checks of existing orders."""
    return _credentials() is not None


def enabled() -> bool:
    """The system admin's switch for new VietQR checkouts (on when no admin configuration exists)."""
    return payment_config.get("payos").enabled


def client():
    credentials = _credentials()
    if credentials is None:
        raise RuntimeError("payOS credentials are not configured")
    from payos import PayOS
    return PayOS(**credentials)


def create_link(order_code: int, amount: int, plan: str, origin: str) -> str:
    from payos.types import CreatePaymentLinkRequest
    parsed = urlparse(origin)
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("Public checkout requires an HTTPS frontend origin")
    result = client().payment_requests.create(CreatePaymentLinkRequest(
        orderCode=order_code,
        amount=amount,
        description=f"RF {plan.upper()} {order_code % 1000000:06d}"[:25],
        cancelUrl=f"{origin}/?payment=cancelled",
        returnUrl=f"{origin}/?payment=returned",
    ))
    link = str(result.checkout_url)
    if urlparse(link).scheme != "https":
        raise ValueError("Invalid payment link")
    return link


def verify_webhook(body: bytes):
    """SDK verifies the signed data using the configured checksum key."""
    return client().webhooks.verify(body)


def get_payment(order_code: int):
    """Read provider status over the authenticated payOS Merchant API."""
    return client().payment_requests.get(order_code)
