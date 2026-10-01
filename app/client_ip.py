"""The client's address behind the trusted proxy path (Phase 24).

Production: browser → Cloudflare → cloudflared → Next.js (127.0.0.1:3001) → FastAPI
(127.0.0.1:8000). FastAPI's peer is always Next.js on loopback, so ``request.client.host``
alone would put every visitor behind 127.0.0.1: one shared login throttle, useless audit
addresses. Cloudflare writes the visitor's address into ``CF-Connecting-IP`` and
overwrites any value a client sent; Next.js forwards the header unchanged.

**Trust boundary.** The header is believed only when the request's peer is a trusted
proxy (by default loopback: Next.js and cloudflared on this server). A request that
reaches FastAPI from anywhere else uses its peer address, whatever headers it carries.
``X-Forwarded-For`` is never used: any client can write it, and Next.js appends to it.

Admin → System settings → Security edits the trusted proxy networks and the header name
(empty turns header trust off).
"""
from functools import lru_cache
import ipaddress

from app import system_config

DEFAULT_TRUSTED = "127.0.0.0/8,::1/128"
DEFAULT_HEADER = "CF-Connecting-IP"


@lru_cache(maxsize=16)
def _networks(raw: str) -> tuple:
    networks = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def trusted_networks() -> tuple:
    return _networks(str(system_config.get("security.trusted_proxies") or DEFAULT_TRUSTED))


def header_name() -> str:
    value = system_config.get("security.client_ip_header")
    return DEFAULT_HEADER if value is None else str(value).strip()


def _address(value: str | None):
    if not value:
        return None
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def is_trusted(peer: str | None) -> bool:
    address = _address(peer)
    if address is None:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in trusted_networks())


def resolve_scope(scope) -> str:
    """The client address for an ASGI scope (see the module docstring)."""
    client = scope.get("client")
    peer = client[0] if client else None
    name = header_name()
    if name and is_trusted(peer):
        wanted = name.lower().encode("latin-1")
        for key, value in scope.get("headers") or ():
            if key == wanted:
                forwarded = _address(value.decode("latin-1"))
                if forwarded is not None:
                    return str(forwarded)
                break
    return peer or "unknown"


def resolve(request) -> str:
    return resolve_scope(request.scope)
