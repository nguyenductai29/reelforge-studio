"""Payment providers behind one checkout and one settlement path (``app/payments.py``).

| Method shown to buyers | Provider | Evidence that an order is paid |
| --- | --- | --- |
| ``vietqr`` (VietQR / bank transfer) | ``payos`` | the signed payOS webhook; the payOS Merchant API |
| ``card`` (credit / debit card) | ``onepay`` | the signed OnePAY IPN; OnePAY QueryDR (a signed browser return is confirmed with QueryDR) |

Every provider turns what it learns into ``Evidence``. ``payments.settle`` then
applies it to the order of that provider only, once: the subscription and the
credits are never touched by provider code. A provider is offered only when the
server has its credentials; nothing here ever returns a credential.
"""
from dataclasses import dataclass

import httpx

from app.payment_providers import onepay

METHODS = {"vietqr": "payos", "card": "onepay"}
STATUSES = frozenset({"paid", "pending", "failed", "cancelled", "expired"})


class ProviderMismatch(ValueError):
    """The provider described another order or another amount than the one asked about."""


@dataclass(frozen=True)
class Evidence:
    """What a provider confirmed about one order."""

    order_code: int
    status: str
    amount_vnd: int | None
    reference: str = ""


def http_client() -> httpx.Client:
    # A factory so tests can answer with httpx.MockTransport.
    return httpx.Client(follow_redirects=False)


class PaymentProvider:
    name = ""
    method = ""

    def configured(self) -> bool:
        raise NotImplementedError

    def checkout(self, *, order_code: int, amount_vnd: int, plan_code: str, origin: str, client_ip: str) -> str:
        """The URL the buyer is sent to."""
        raise NotImplementedError

    def lookup(self, order_code: int, amount_vnd: int) -> Evidence:
        """The order's state, asked of the provider server to server."""
        raise NotImplementedError


def _payos():
    # Imported on use: app.billing reads the server's bootstrap file.
    from app import billing
    return billing


class PayOSProvider(PaymentProvider):
    """VietQR bank transfers through payOS (``app/billing.py``)."""

    name, method = "payos", "vietqr"

    def configured(self) -> bool:
        return _payos().configured()

    def checkout(self, *, order_code, amount_vnd, plan_code, origin, client_ip):
        return _payos().create_link(order_code, amount_vnd, plan_code, origin)

    def lookup(self, order_code, amount_vnd):
        found = _payos().get_payment(order_code)
        if int(found.order_code) != order_code or int(found.amount) != amount_vnd:
            raise ProviderMismatch("Provider order mismatch")
        status = {"PAID": "paid", "CANCELLED": "cancelled", "EXPIRED": "expired"}.get(found.status, "pending")
        if status == "paid" and int(found.amount_paid) < amount_vnd:
            raise ProviderMismatch("Payment amount mismatch")
        return Evidence(order_code, status, int(found.amount_paid) if status == "paid" else None, str(found.id))


class OnePayProvider(PaymentProvider):
    """Credit and debit cards through OnePAY (``app/payment_providers/onepay.py``)."""

    name, method = "onepay", "card"

    def configured(self) -> bool:
        return onepay.configured()

    def checkout(self, *, order_code, amount_vnd, plan_code, origin, client_ip):
        config = onepay.OnePayConfig.from_environment()
        return onepay.checkout_url(config, merch_txn_ref=str(order_code), amount_vnd=amount_vnd,
                                   order_info=f"RF {plan_code.upper()} {order_code}",
                                   return_url=f"{origin}/api/billing/onepay/return", client_ip=client_ip,
                                   again_link=f"{origin}/billing")

    def evidence(self, params) -> Evidence:
        """A signed OnePAY return or IPN as evidence; ``OnePayError`` when the signature does not match."""
        result = onepay.read_result(params, onepay.OnePayConfig.from_environment())
        return _evidence(result)

    def lookup(self, order_code, amount_vnd):
        with http_client() as client:
            result = onepay.query(onepay.OnePayConfig.from_environment(), str(order_code), client=client)
        evidence = _evidence(result)
        if evidence.order_code != order_code:
            raise ProviderMismatch("Provider order mismatch")
        return evidence

    @staticmethod
    def can_confirm() -> bool:
        """Whether a browser return can be confirmed server to server (QueryDR credentials are set)."""
        try:
            return onepay.OnePayConfig.from_environment().can_query
        except onepay.OnePayError:
            return False


def _evidence(result: onepay.OnePayResult) -> Evidence:
    if not result.merch_txn_ref.isdigit():
        raise ProviderMismatch("Unknown order reference")
    return Evidence(int(result.merch_txn_ref), result.status, result.amount_vnd, result.transaction_no)


PROVIDERS: dict[str, PaymentProvider] = {"payos": PayOSProvider(), "onepay": OnePayProvider()}


def for_method(method: str) -> PaymentProvider:
    return PROVIDERS[METHODS[method]]


def provider(name: str) -> PaymentProvider | None:
    return PROVIDERS.get(name)


def readiness() -> list[dict]:
    """Which providers the server can use; never their credentials."""
    return [{"provider": item.name, "method": item.method, "configured": item.configured()}
            for item in PROVIDERS.values()]


def available_methods() -> list[dict]:
    """The payment methods a buyer may choose: configured providers only."""
    return [{"id": item.method, "provider": item.name} for item in PROVIDERS.values() if item.configured()]
