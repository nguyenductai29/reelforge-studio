"""Manual VietQR: a bank-transfer QR for each order, confirmed by a system admin (Phase 20B).

The QR is the NAPAS VietQR payload (EMVCo merchant-presented format) that every
Vietnamese banking app scans: the bank's BIN, the account number, the exact
amount and the transfer content, closed by a CRC-16/CCITT checksum. It is built
and drawn here, on the server (``segno``); no image service is called and no
secret is involved, because everything in it is shown to the buyer anyway.

The transfer content is the configured prefix followed by the order's 13-digit
code (``RF1234567890123``), so every order is told apart even when several
buyers pay at once. Generating a QR is never evidence of payment: an order is
paid only when a system admin confirms the money arrived (``POST
/api/admin/payments/{id}/confirm``), through the shared settlement path.
"""
import re

import segno

from app import system_config

# NAPAS BINs of common Vietnamese banks (the six digits a VietQR names the bank by). The admin may also type a BIN.
BANKS = (
    ("970436", "Vietcombank"), ("970415", "VietinBank"), ("970418", "BIDV"), ("970405", "Agribank"),
    ("970407", "Techcombank"), ("970422", "MB Bank"), ("970416", "ACB"), ("970432", "VPBank"),
    ("970423", "TPBank"), ("970403", "Sacombank"), ("970441", "VIB"), ("970443", "SHB"), ("970437", "HDBank"),
    ("970448", "OCB"), ("970426", "MSB"), ("970440", "SeABank"), ("970431", "Eximbank"), ("970449", "LPBank"),
    ("970428", "Nam A Bank"), ("970409", "Bac A Bank"), ("970454", "BVBank"), ("970412", "PVcomBank"),
    ("970452", "KienlongBank"), ("970427", "VietABank"), ("970425", "ABBANK"),
)
FIELDS = ("bank_bin", "bank_name", "account_number", "account_name", "transfer_prefix", "note", "sla_message")
SAMPLE_AMOUNT = 100_000
_BIN = re.compile(r"[0-9]{6}\Z")
_ACCOUNT = re.compile(r"[0-9A-Za-z]{4,19}\Z")
_PREFIX = re.compile(r"[A-Z0-9]{1,8}\Z")


def crc16(data: str) -> str:
    """CRC-16/CCITT-FALSE (polynomial 0x1021, initial 0xFFFF), as four uppercase hex digits."""
    crc = 0xFFFF
    for byte in data.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def _tlv(tag: str, value: str) -> str:
    if len(value) > 99:
        raise ValueError("VietQR field too long")
    return f"{tag}{len(value):02d}{value}"


def payload(bank_bin: str, account_number: str, amount_vnd: int, content: str) -> str:
    """The VietQR string for a transfer of exactly ``amount_vnd`` to the account, with ``content``."""
    if not _BIN.fullmatch(bank_bin) or not _ACCOUNT.fullmatch(account_number):
        raise ValueError("Invalid bank account")
    if not isinstance(amount_vnd, int) or not 0 < amount_vnd < 10 ** 13:
        raise ValueError("Invalid amount")
    if not re.fullmatch(r"[A-Za-z0-9]{1,25}", content):
        raise ValueError("Invalid transfer content")
    beneficiary = _tlv("00", bank_bin) + _tlv("01", account_number)
    merchant = _tlv("00", "A000000727") + _tlv("01", beneficiary) + _tlv("02", "QRIBFTTA")
    body = (_tlv("00", "01") + _tlv("01", "12") + _tlv("38", merchant) + _tlv("53", "704")
            + _tlv("54", str(amount_vnd)) + _tlv("58", "VN") + _tlv("62", _tlv("08", content)) + "6304")
    return body + crc16(body)


def image(data: str) -> str:
    """The QR as an SVG data URI (drawn locally)."""
    return segno.make(data, error="m", micro=False).svg_data_uri(scale=6, border=2)


def transfer_content(prefix: str, order_code: int) -> str:
    return f"{prefix}{order_code}"


def settings() -> dict:
    """The manual VietQR configuration in use (Admin → Payments → VietQR)."""
    return {name: system_config.get(f"payments.bank_qr.{name}") or "" for name in FIELDS}


def problems(values: dict) -> list[str]:
    """Missing or invalid fields, by name; empty when a QR can be made."""
    found = []
    if not _BIN.fullmatch(values.get("bank_bin") or ""):
        found.append("bank_bin")
    if not _ACCOUNT.fullmatch(values.get("account_number") or ""):
        found.append("account_number")
    if not (values.get("account_name") or "").strip():
        found.append("account_name")
    if not _PREFIX.fullmatch(values.get("transfer_prefix") or ""):
        found.append("transfer_prefix")
    return found


def configured() -> bool:
    return not problems(settings())


def bank_name(values: dict) -> str:
    return values.get("bank_name") or dict(BANKS).get(values.get("bank_bin") or "", "")


def details(values: dict, *, amount_vnd: int, content: str) -> dict:
    """What the buyer needs to pay: the QR and the same facts as text, for apps that cannot scan."""
    data = payload(values["bank_bin"], values["account_number"], amount_vnd, content)
    return {"bank_bin": values["bank_bin"], "bank_name": bank_name(values), "account_number": values["account_number"],
            "account_name": values["account_name"], "amount_vnd": amount_vnd, "content": content, "payload": data,
            "qr": image(data), "note": values.get("note") or "", "sla_message": values.get("sla_message") or ""}


def preview(values: dict) -> dict:
    """A sample QR for the admin screen: 100,000 VND with the content ``<prefix>TEST01``."""
    return details(values, amount_vnd=SAMPLE_AMOUNT, content=f"{values['transfer_prefix']}TEST01")
