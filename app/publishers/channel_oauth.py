"""Workspace-scoped OAuth for TikTok and Facebook Pages, for their official publishing APIs only.

Mirrors ``google_oauth.py``: the API layer authenticates the user; this module
binds each callback to that user and workspace with a ten-minute, single-use
state (stored hashed), consumes the state before calling the token endpoint, and
encrypts every token at rest with ``REELFORGE_TOKEN_ENCRYPTION_KEY`` (Fernet).
No token ever leaves the server or appears in a log or an API response.

* **TikTok** (Login Kit v2): scopes ``user.info.basic`` and ``video.upload``.
  Access tokens last about a day and are refreshed with the refresh token (about
  a year); once the refresh token expires the owner must authorize again.
  ``video.upload`` sends videos to the creator's TikTok inbox as drafts; nothing
  is posted without the creator finishing it in the TikTok app.
* **Facebook** (Facebook Login): scopes ``pages_show_list``,
  ``pages_read_engagement`` and ``pages_manage_posts``. The short-lived user token
  is exchanged for a long-lived one, the Pages the user manages are listed, and
  the Page access tokens are stored encrypted. The owner chooses one Page (chosen
  automatically when there is only one); Reels are published to that Page.

Configuration (server environment): ``TIKTOK_CLIENT_KEY``, ``TIKTOK_CLIENT_SECRET``,
``TIKTOK_REDIRECT_URI``; ``FACEBOOK_APP_ID``, ``FACEBOOK_APP_SECRET``,
``FACEBOOK_REDIRECT_URI``; and ``REELFORGE_TOKEN_ENCRYPTION_KEY``.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import secrets
from typing import Any
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
from sqlalchemy import DateTime, ForeignKey, String, Text, delete, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models import Base, Membership
from app.publishers.facebook import GRAPH_URL, API_VERSION

CHANNELS = ("tiktok", "facebook")
STATE_LIFETIME = timedelta(minutes=10)
EXPIRY_MARGIN = timedelta(seconds=120)
TIKTOK_AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_USER_INFO = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name"
TIKTOK_SCOPES = ("user.info.basic", "video.upload")
TIKTOK_UPLOAD_SCOPE = "video.upload"
FACEBOOK_AUTHORIZE = f"https://www.facebook.com/{API_VERSION}/dialog/oauth"
FACEBOOK_TOKEN = f"{GRAPH_URL}/oauth/access_token"
FACEBOOK_PAGES = f"{GRAPH_URL}/me/accounts?fields=id,name,access_token,tasks&limit=100"
FACEBOOK_SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts")
# A Page role that can publish: Meta lists it as the CREATE_CONTENT task.
FACEBOOK_PUBLISH_TASK = "CREATE_CONTENT"
_ENV = {"tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI"),
        "facebook": ("FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET", "FACEBOOK_REDIRECT_URI")}


class ChannelOAuthError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class ChannelConfig:
    channel: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    encryption_key: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            uri = urlsplit(self.redirect_uri)
            local_http = uri.scheme == "http" and uri.hostname in {"localhost", "127.0.0.1"}
            Fernet(self.encryption_key.encode("ascii"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ChannelOAuthError("not_configured", f"{self.channel} OAuth configuration is invalid") from exc
        if (self.channel not in CHANNELS
                or not isinstance(self.client_id, str) or not self.client_id.strip()
                or not isinstance(self.client_secret, str) or not self.client_secret.strip()
                or not uri.netloc or (uri.scheme != "https" and not local_http)
                or uri.username or uri.password or uri.query or uri.fragment):
            raise ChannelOAuthError("not_configured", f"{self.channel} OAuth configuration is invalid")

    @classmethod
    def from_environment(cls, channel: str) -> "ChannelConfig":
        if channel not in CHANNELS:
            raise ChannelOAuthError("unsupported_channel", "Unsupported channel")
        client_id, secret, redirect = _ENV[channel]
        return cls(channel, os.environ.get(client_id, ""), os.environ.get(secret, ""),
                   os.environ.get(redirect, ""), os.environ.get("REELFORGE_TOKEN_ENCRYPTION_KEY", ""))


def configured(channel: str) -> bool:
    try:
        ChannelConfig.from_environment(channel)
    except ChannelOAuthError:
        return False
    return True


class ChannelOAuthState(Base):
    __tablename__ = "channel_oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChannelConnection(Base):
    """One workspace's grant for one channel; every token column is Fernet ciphertext."""

    __tablename__ = "channel_connections"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    # TikTok: the creator's open_id and display name. Facebook: the chosen Page.
    account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # TikTok: the user access token. Facebook: the chosen Page's access token.
    access_token_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Facebook: the Pages the user can publish to, with their tokens, as encrypted JSON.
    accounts_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[str] = mapped_column(Text, default="")
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class AuthorizationStart:
    url: str = field(repr=False)
    state: str = field(repr=False)
    expires_at: datetime


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _encrypt(config: ChannelConfig, secret: str) -> str:
    return Fernet(config.encryption_key.encode("ascii")).encrypt(secret.encode("utf-8")).decode("ascii")


def _decrypt(config: ChannelConfig, ciphertext: str | None) -> str:
    if not ciphertext:
        raise ChannelOAuthError("authorization_required", "The channel has no stored authorization")
    try:
        return Fernet(config.encryption_key.encode("ascii")).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, AttributeError) as exc:
        raise ChannelOAuthError("token_unavailable", "Stored channel authorization cannot be decrypted") from exc


def scopes(connection: ChannelConnection) -> frozenset[str]:
    return frozenset(item for item in (connection.scope or "").replace(",", " ").split() if item)


def connection_generation(connection: ChannelConnection) -> str:
    """Stable UTC marker for the consent that created this connection (like YouTube's)."""
    return _utc(connection.connected_at).isoformat(timespec="microseconds")


def status(db: Session, channel: str, workspace_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """What the Channels page shows; never a token.

    ``configuration_required``: the server lacks the app credentials or the
    encryption key. ``not_connected``: nobody authorized yet.
    ``authorization_required``: the grant expired, lacks the publishing scope,
    or (Facebook) no Page is chosen yet. ``connected``: ready to publish.
    """
    now = now or datetime.now(timezone.utc)
    connection = db.get(ChannelConnection, (workspace_id, channel))
    result: dict[str, Any] = {"channel": channel, "account_name": None, "connected_at": None, "reason": None}
    if not configured(channel):
        result["status"] = "configuration_required"
        return result
    if connection is None:
        result["status"] = "not_connected"
        return result
    result.update(account_name=connection.account_name, connected_at=_utc(connection.connected_at).isoformat())
    reason = None
    if channel == "tiktok":
        refresh_expires = _utc(connection.refresh_expires_at)
        if TIKTOK_UPLOAD_SCOPE not in scopes(connection):
            reason = "missing_scope"
        elif not connection.refresh_token_ciphertext or (refresh_expires is not None and refresh_expires <= now):
            reason = "expired"
    elif not connection.access_token_ciphertext or not connection.account_id:
        reason = "page_required"
        try:
            config = ChannelConfig.from_environment(channel)
            result["pages"] = [{"id": page["id"], "name": page["name"]} for page in _pages(config, connection)]
        except ChannelOAuthError:
            result["pages"] = []
    result["status"] = "authorization_required" if reason else "connected"
    result["reason"] = reason
    return result


def begin_authorization(db: Session, config: ChannelConfig, *, workspace_id: str, user_id: str) -> AuthorizationStart:
    """Create a ten-minute state; the caller commits before redirecting the browser."""
    membership = db.get(Membership, (user_id, workspace_id))
    if membership is None or membership.role != "owner":
        raise ChannelOAuthError("forbidden", "Workspace owner required")
    state = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + STATE_LIFETIME
    db.add(ChannelOAuthState(state_hash=sha256(state.encode("ascii")).hexdigest(), channel=config.channel,
                             workspace_id=workspace_id, user_id=user_id, redirect_uri=config.redirect_uri,
                             created_at=now, expires_at=expires_at))
    if config.channel == "tiktok":
        query = {"client_key": config.client_id, "scope": ",".join(TIKTOK_SCOPES), "response_type": "code",
                 "redirect_uri": config.redirect_uri, "state": state}
        return AuthorizationStart(f"{TIKTOK_AUTHORIZE}?{urlencode(query)}", state, expires_at)
    query = {"client_id": config.client_id, "redirect_uri": config.redirect_uri, "state": state,
             "response_type": "code", "scope": ",".join(FACEBOOK_SCOPES)}
    return AuthorizationStart(f"{FACEBOOK_AUTHORIZE}?{urlencode(query)}", state, expires_at)


def _post(client: httpx.Client, url: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        response = client.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"},
                               follow_redirects=False)
    except httpx.RequestError as exc:
        raise ChannelOAuthError("provider_unavailable", "The token endpoint is unavailable", retryable=True) from exc
    return _body(response)


def _get(client: httpx.Client, url: str, token: str) -> dict[str, Any]:
    try:
        response = client.get(url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=False)
    except httpx.RequestError as exc:
        raise ChannelOAuthError("provider_unavailable", "The account endpoint is unavailable", retryable=True) from exc
    return _body(response)


def _body(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise ChannelOAuthError("token_error", f"The token endpoint returned HTTP {response.status_code}",
                                retryable=response.status_code == 429 or response.status_code >= 500,
                                http_status=response.status_code)
    try:
        body = response.json()
    except ValueError as exc:
        raise ChannelOAuthError("invalid_response", "The token endpoint returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise ChannelOAuthError("invalid_response", "The token endpoint returned an invalid response")
    error = body.get("error")
    # TikTok reports token errors with HTTP 200; its API wraps success as {"error": {"code": "ok"}}.
    if isinstance(error, str) or (isinstance(error, dict) and error.get("code") not in (None, "ok")):
        raise ChannelOAuthError("token_error", "The provider rejected the authorization")
    return body


def _seconds(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _consume_state(db: Session, config: ChannelConfig, *, state: str, current_user_id: str) -> str:
    if not isinstance(state, str) or not state or len(state) > 256:
        raise ChannelOAuthError("invalid_state", "OAuth callback is missing a valid state")
    digest = sha256(state.encode("utf-8")).hexdigest()
    pending = db.get(ChannelOAuthState, digest)
    membership = db.get(Membership, (current_user_id, pending.workspace_id)) if pending is not None else None
    now = datetime.now(timezone.utc)
    if (pending is None or pending.channel != config.channel or pending.user_id != current_user_id
            or pending.consumed_at is not None or _utc(pending.expires_at) <= now
            or pending.redirect_uri != config.redirect_uri or membership is None or membership.role != "owner"):
        db.rollback()
        raise ChannelOAuthError("invalid_state", "OAuth state is expired or does not match the user")
    workspace_id = pending.workspace_id
    changed = db.execute(update(ChannelOAuthState)
                         .where(ChannelOAuthState.state_hash == digest, ChannelOAuthState.consumed_at.is_(None),
                                ChannelOAuthState.expires_at > now)
                         .values(consumed_at=now).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise ChannelOAuthError("invalid_state", "OAuth state was already used")
    db.commit()
    return workspace_id


def _tiktok_tokens(body: dict[str, Any]) -> dict[str, Any]:
    access, refresh = body.get("access_token"), body.get("refresh_token")
    expires_in, refresh_in = _seconds(body.get("expires_in")), _seconds(body.get("refresh_expires_in"))
    scope = body.get("scope") if isinstance(body.get("scope"), str) else ""
    if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
            or expires_in is None or refresh_in is None):
        raise ChannelOAuthError("invalid_response", "TikTok token response is incomplete")
    now = datetime.now(timezone.utc)
    return {"access": access, "refresh": refresh, "expires_at": now + timedelta(seconds=expires_in),
            "refresh_expires_at": now + timedelta(seconds=refresh_in), "scope": scope,
            "open_id": body.get("open_id") if isinstance(body.get("open_id"), str) else None}


def _tiktok_name(client: httpx.Client, token: str) -> str | None:
    try:
        user = _get(client, TIKTOK_USER_INFO, token).get("data", {}).get("user", {})
    except (ChannelOAuthError, AttributeError):
        return None
    name = user.get("display_name") if isinstance(user, dict) else None
    return name[:255] if isinstance(name, str) and name.strip() else None


def _facebook_pages(client: httpx.Client, user_token: str) -> list[dict[str, str]]:
    data = _get(client, FACEBOOK_PAGES, user_token).get("data")
    pages = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        page_id, name, token, tasks = item.get("id"), item.get("name"), item.get("access_token"), item.get("tasks")
        if (isinstance(page_id, str) and page_id.isdigit() and isinstance(token, str) and token
                and (not isinstance(tasks, list) or FACEBOOK_PUBLISH_TASK in tasks)):
            pages.append({"id": page_id, "name": (name if isinstance(name, str) else page_id)[:255], "token": token})
    return pages


def _pages(config: ChannelConfig, connection: ChannelConnection) -> list[dict[str, str]]:
    if not connection.accounts_ciphertext:
        return []
    try:
        value = json.loads(_decrypt(config, connection.accounts_ciphertext))
    except ValueError as exc:
        raise ChannelOAuthError("token_unavailable", "Stored Pages cannot be read") from exc
    return [page for page in value if isinstance(page, dict)] if isinstance(value, list) else []


def complete_authorization(db: Session, config: ChannelConfig, *, state: str, code: str, current_user_id: str,
                           client: httpx.Client) -> dict[str, Any]:
    """Consume the state once, exchange the code outside a DB transaction, then store the grant."""
    if not isinstance(code, str) or not code or len(code) > 4096:
        raise ChannelOAuthError("invalid_state", "OAuth callback is missing a valid code")
    workspace_id = _consume_state(db, config, state=state, current_user_id=current_user_id)
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {"connected_at": now, "updated_at": now, "refresh_token_ciphertext": None,
                              "refresh_expires_at": None, "accounts_ciphertext": None, "access_token_ciphertext": None,
                              "account_id": None, "account_name": None, "expires_at": None}
    if config.channel == "tiktok":
        tokens = _tiktok_tokens(_post(client, TIKTOK_TOKEN, {
            "client_key": config.client_id, "client_secret": config.client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": config.redirect_uri}))
        values.update(access_token_ciphertext=_encrypt(config, tokens["access"]),
                      refresh_token_ciphertext=_encrypt(config, tokens["refresh"]), expires_at=tokens["expires_at"],
                      refresh_expires_at=tokens["refresh_expires_at"], scope=tokens["scope"],
                      account_id=tokens["open_id"], account_name=_tiktok_name(client, tokens["access"]))
    else:
        short = _post(client, FACEBOOK_TOKEN, {"client_id": config.client_id, "client_secret": config.client_secret,
                                               "redirect_uri": config.redirect_uri, "code": code})
        if not isinstance(short.get("access_token"), str) or not short["access_token"]:
            raise ChannelOAuthError("invalid_response", "Facebook token response is incomplete")
        long_lived = _post(client, FACEBOOK_TOKEN, {"grant_type": "fb_exchange_token", "client_id": config.client_id,
                                                    "client_secret": config.client_secret,
                                                    "fb_exchange_token": short["access_token"]})
        user_token = long_lived.get("access_token")
        if not isinstance(user_token, str) or not user_token:
            raise ChannelOAuthError("invalid_response", "Facebook did not return a long-lived token")
        pages = _facebook_pages(client, user_token)
        if not pages:
            raise ChannelOAuthError("no_pages", "No Facebook Page you manage can publish content")
        expires_in = _seconds(long_lived.get("expires_in"))
        values.update(accounts_ciphertext=_encrypt(config, json.dumps(pages)), scope=",".join(FACEBOOK_SCOPES),
                      expires_at=now + timedelta(seconds=expires_in) if expires_in else None)
        if len(pages) == 1:
            values.update(account_id=pages[0]["id"], account_name=pages[0]["name"],
                          access_token_ciphertext=_encrypt(config, pages[0]["token"]))
    connection = db.get(ChannelConnection, (workspace_id, config.channel))
    if connection is None:
        connection = ChannelConnection(workspace_id=workspace_id, channel=config.channel)
        db.add(connection)
    for key, value in values.items():
        setattr(connection, key, value)
    db.commit()
    return status(db, config.channel, workspace_id)


def select_facebook_page(db: Session, config: ChannelConfig, *, workspace_id: str, page_id: str) -> None:
    """Publish to one of the Pages listed at authorization; the caller commits."""
    connection = db.get(ChannelConnection, (workspace_id, "facebook"))
    if connection is None:
        raise ChannelOAuthError("not_connected", "Connect Facebook first")
    page = next((page for page in _pages(config, connection) if page.get("id") == page_id), None)
    if page is None:
        raise ChannelOAuthError("unknown_page", "This Page is not available to the connected account")
    connection.account_id, connection.account_name = page["id"], page["name"]
    connection.access_token_ciphertext = _encrypt(config, page["token"])
    # Another Page is another destination: queued uploads for the old one must not continue.
    connection.connected_at = connection.updated_at = datetime.now(timezone.utc)


def tiktok_access_token(db: Session, config: ChannelConfig, *, workspace_id: str, client: httpx.Client,
                        expected_generation: str | None = None) -> tuple[str, frozenset[str]]:
    """A usable TikTok access token and its granted scopes, refreshed when it expires within two minutes."""
    connection = db.get(ChannelConnection, (workspace_id, "tiktok"))
    if connection is None:
        db.rollback()
        raise ChannelOAuthError("not_connected", "Workspace has no TikTok connection")
    if expected_generation is not None and connection_generation(connection) != expected_generation:
        db.rollback()
        raise ChannelOAuthError("connection_changed", "TikTok connection changed after the upload was queued")
    granted = scopes(connection)
    expires = _utc(connection.expires_at)
    now = datetime.now(timezone.utc)
    if expires is not None and expires > now + EXPIRY_MARGIN and connection.access_token_ciphertext:
        return _decrypt(config, connection.access_token_ciphertext), granted
    refresh_expires = _utc(connection.refresh_expires_at)
    if refresh_expires is not None and refresh_expires <= now:
        db.rollback()
        raise ChannelOAuthError("authorization_required", "TikTok authorization expired; connect again")
    previous, previous_scope = connection.refresh_token_ciphertext, connection.scope
    connected_at = connection.connected_at
    refresh = _decrypt(config, previous)
    db.rollback()  # release the session before calling TikTok
    tokens = _tiktok_tokens(_post(client, TIKTOK_TOKEN, {"client_key": config.client_id,
                                                         "client_secret": config.client_secret,
                                                         "grant_type": "refresh_token", "refresh_token": refresh}))
    changed = db.execute(update(ChannelConnection)
                         .where(ChannelConnection.workspace_id == workspace_id, ChannelConnection.channel == "tiktok",
                                ChannelConnection.refresh_token_ciphertext == previous,
                                ChannelConnection.connected_at == connected_at)
                         .values(access_token_ciphertext=_encrypt(config, tokens["access"]),
                                 refresh_token_ciphertext=_encrypt(config, tokens["refresh"]),
                                 expires_at=tokens["expires_at"], refresh_expires_at=tokens["refresh_expires_at"],
                                 scope=tokens["scope"] or previous_scope, updated_at=datetime.now(timezone.utc)))
    if changed.rowcount != 1:
        db.rollback()
        raise ChannelOAuthError("refresh_conflict", "TikTok connection changed during refresh", retryable=True)
    db.commit()
    return tokens["access"], frozenset(item for item in tokens["scope"].replace(",", " ").split() if item) or granted


def facebook_page_token(db: Session, config: ChannelConfig, *, workspace_id: str,
                        expected_generation: str | None = None) -> tuple[str, str]:
    """(Page ID, Page access token) of the chosen Page."""
    connection = db.get(ChannelConnection, (workspace_id, "facebook"))
    if connection is None:
        raise ChannelOAuthError("not_connected", "Workspace has no Facebook connection")
    if expected_generation is not None and connection_generation(connection) != expected_generation:
        raise ChannelOAuthError("connection_changed", "Facebook connection changed after the upload was queued")
    if not connection.account_id or not connection.access_token_ciphertext:
        raise ChannelOAuthError("authorization_required", "Choose a Facebook Page first")
    return connection.account_id, _decrypt(config, connection.access_token_ciphertext)


def disconnect(db: Session, channel: str, *, workspace_id: str) -> None:
    """Delete local tokens; the grant can also be removed in the platform's app settings."""
    db.execute(delete(ChannelConnection).where(ChannelConnection.workspace_id == workspace_id,
                                               ChannelConnection.channel == channel))
    db.commit()
