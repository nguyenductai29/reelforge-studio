"""payOS adapter. Credentials live in the private bootstrap file, not in SQL."""
from urllib.parse import urlparse
from app.db import config


def configured() -> bool:
    data = config.get("payos") or {}
    return all(data.get(key) for key in ("client_id", "api_key", "checksum_key"))


def client():
    if not configured():
        raise RuntimeError("payOS credentials are not configured")
    from payos import PayOS
    return PayOS(**{key: config["payos"][key] for key in ("client_id", "api_key", "checksum_key")})


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
