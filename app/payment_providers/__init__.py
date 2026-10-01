"""Payment providers behind one checkout and one settlement path (``app/payments.py``).

| Method shown to buyers | Provider | Evidence that an order is paid |
| --- | --- | --- |
| ``vietqr`` (VietQR / bank transfer) | ``payos`` (automatic) | the signed payOS webhook; the payOS Merchant API |
| ``vietqr`` (VietQR / bank transfer) | ``bank_qr`` (manual, Phase 20) | a system admin's confirmation that the money arrived |
| ``card`` (credit / debit card) | ``onepay`` | the signed OnePAY IPN; OnePAY QueryDR (a signed browser return is confirmed with QueryDR) |

Every provider turns what it learns into ``Evidence``. ``payments.settle`` then
applies it to the order of that provider only, once: the subscription and the
credits are never touched by provider code. Credentials come from
``app.payment_config`` (admin-managed, else bootstrap/env). A provider is offered
for new checkouts only when the system admin has it enabled and its credentials
are valid; callbacks and status queries of existing orders need only valid
credentials. Nothing here ever returns a credential.
"""
from dataclasses import dataclass

import httpx

from app.payment_providers import onepay

# Which method each provider serves. VietQR is payOS or the manual bank QR, by the admin's VietQR mode.
PROVIDER_METHOD = {"payos": "vietqr", "bank_qr": "vietqr", "onepay": "card"}
METHOD_ORDER = ("vietqr", "card")
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
        """Whether the credentials are usable (callbacks and status queries need only this)."""
        raise NotImplementedError

    def enabled(self) -> bool:
        """The system admin's switch for new checkouts."""
        return True

    def offered(self) -> bool:
        return self.enabled() and self.configured()

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

    def enabled(self) -> bool:
        return _payos().enabled()

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

    @staticmethod
    def config() -> onepay.OnePayConfig:
        """The resolved OnePAY configuration (``app.payment_config``); ``OnePayError`` when incomplete."""
        from app import payment_config
        return payment_config.onepay_config(payment_config.get("onepay"))

    def configured(self) -> bool:
        try:
            self.config()
        except onepay.OnePayError:
            return False
        return True

    def enabled(self) -> bool:
        from app import payment_config
        return payment_config.get("onepay").enabled

    def checkout(self, *, order_code, amount_vnd, plan_code, origin, client_ip):
        config = self.config()
        return onepay.checkout_url(config, merch_txn_ref=str(order_code), amount_vnd=amount_vnd,
                                   order_info=f"RF {plan_code.upper()} {order_code}",
                                   return_url=f"{origin}/api/billing/onepay/return", client_ip=client_ip,
                                   again_link=f"{origin}/billing")

    def evidence(self, params) -> Evidence:
        """A signed OnePAY return or IPN as evidence; ``OnePayError`` when the signature does not match."""
        result = onepay.read_result(params, self.config())
        return _evidence(result)

    def lookup(self, order_code, amount_vnd):
        with http_client() as client:
            result = onepay.query(self.config(), str(order_code), client=client)
        evidence = _evidence(result)
        if evidence.order_code != order_code:
            raise ProviderMismatch("Provider order mismatch")
        return evidence

    def can_confirm(self) -> bool:
        """Whether a browser return can be confirmed server to server (QueryDR credentials are set)."""
        try:
            return self.config().can_query
        except onepay.OnePayError:
            return False


class BankQRProvider(PaymentProvider):
    """Manual VietQR (``app/bank_qr.py``): the buyer transfers to the studio's bank account; an admin confirms."""

    name, method = "bank_qr", "vietqr"

    def configured(self) -> bool:
        from app import bank_qr
        return bank_qr.configured()

    def enabled(self) -> bool:
        from app import system_config
        return bool(system_config.get("payments.bank_qr.enabled"))

    def lookup(self, order_code, amount_vnd):
        raise ProviderMismatch("Manual transfers are confirmed by an administrator")


def vietqr_mode() -> str:
    """``manual`` (bank QR confirmed by an admin) or ``payos`` (automatic, the default)."""
    from app import system_config
    return "manual" if system_config.get("payments.vietqr_mode") == "manual" else "payos"


def _evidence(result: onepay.OnePayResult) -> Evidence:
    if not result.merch_txn_ref.isdigit():
        raise ProviderMismatch("Unknown order reference")
    return Evidence(int(result.merch_txn_ref), result.status, result.amount_vnd, result.transaction_no)


PROVIDERS: dict[str, PaymentProvider] = {"payos": PayOSProvider(), "bank_qr": BankQRProvider(),
                                         "onepay": OnePayProvider()}


def for_method(method: str) -> PaymentProvider:
    """The provider that takes new checkouts for ``method`` now."""
    if method == "vietqr":
        return PROVIDERS["bank_qr" if vietqr_mode() == "manual" else "payos"]
    return PROVIDERS["onepay"]


def provider(name: str) -> PaymentProvider | None:
    return PROVIDERS.get(name)


def readiness() -> list[dict]:
    """Which providers the server can use and offers, and where their configuration comes from; never values."""
    from app import payment_config
    out = []
    for item in PROVIDERS.values():
        configured, enabled = item.configured(), item.enabled()
        active = for_method(item.method) is item
        source = payment_config.get(item.name).source if item.name in payment_config.PROVIDERS else (
            "admin" if configured else "missing")
        out.append({"provider": item.name, "method": item.method, "configured": configured, "enabled": enabled,
                    "active": active, "available": configured and enabled and active, "source": source})
    return out


def available_methods() -> list[dict]:
    """The payment methods a buyer may choose: enabled providers with valid credentials only."""
    return [{"id": method, "provider": item.name} for method in METHOD_ORDER
            if (item := for_method(method)).offered()]
