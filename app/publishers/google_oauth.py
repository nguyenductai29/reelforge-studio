"""Workspace-scoped Google OAuth for the YouTube upload permission.

The API layer authenticates the current user and supplies their ID. This
module binds callbacks to that user, encrypts secrets at rest, and deliberately
consumes callback state before contacting Google's token endpoint.
"""

from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
import secrets
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
from sqlalchemy import DateTime, ForeignKey, String, Text, delete, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import master_key
from app.models import Base, Membership
from app.publishers.youtube import UPLOAD_SCOPE
from app import system_config


AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
STATE_LIFETIME = timedelta(minutes=10)
EXPIRY_MARGIN = timedelta(seconds=60)


class OAuthError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class GoogleOAuthConfig:
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
            raise OAuthError("not_configured", "YouTube OAuth configuration is invalid") from exc
        if (
            not isinstance(self.client_id, str) or not self.client_id.strip()
            or not isinstance(self.client_secret, str) or not self.client_secret.strip()
            or not uri.netloc or (uri.scheme != "https" and not local_http)
            or uri.username or uri.password or uri.query or uri.fragment
        ):
            raise OAuthError("not_configured", "YouTube OAuth configuration is invalid")

    @classmethod
    def from_environment(cls) -> "GoogleOAuthConfig":
        return cls(
            client_id=system_config.env("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=system_config.env("GOOGLE_OAUTH_CLIENT_SECRET"),
            # An explicit override (admin, then environment), else derived from frontend_origin.
            redirect_uri=system_config.redirect_uri("youtube"),
            encryption_key=master_key.load(),
        )


class YouTubeOAuthState(Base):
    __tablename__ = "youtube_oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    verifier_ciphertext: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class YouTubeConnection(Base):
    __tablename__ = "youtube_connections"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    access_token_ciphertext: Mapped[str] = mapped_column(Text)
    refresh_token_ciphertext: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str] = mapped_column(Text)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class AuthorizationStart:
    url: str = field(repr=False)
    state: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class ConnectionStatus:
    workspace_id: str
    expires_at: datetime
    connected_at: datetime
    scope: str


@dataclass(frozen=True, repr=False)
class _TokenResponse:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scope: str


def _fernet(config: GoogleOAuthConfig) -> Fernet:
    return Fernet(config.encryption_key.encode("ascii"))


def _encrypt(config: GoogleOAuthConfig, secret: str) -> str:
    return _fernet(config).encrypt(secret.encode("utf-8")).decode("ascii")


def _decrypt(config: GoogleOAuthConfig, ciphertext: str) -> str:
    try:
        return _fernet(config).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, AttributeError) as exc:
        raise OAuthError("token_unavailable", "Stored YouTube authorization cannot be decrypted") from exc


def _status(connection: YouTubeConnection) -> ConnectionStatus:
    return ConnectionStatus(connection.workspace_id, connection.expires_at, connection.connected_at, connection.scope)


def connection_status(db: Session, *, workspace_id: str) -> ConnectionStatus | None:
    connection = db.get(YouTubeConnection, workspace_id)
    return _status(connection) if connection is not None else None


def connection_generation(connection: ConnectionStatus | YouTubeConnection) -> str:
    """Stable UTC marker for the consent that created this connection."""
    connected_at = connection.connected_at
    if connected_at.tzinfo is None:
        connected_at = connected_at.replace(tzinfo=timezone.utc)
    return connected_at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def begin_authorization(
    db: Session,
    config: GoogleOAuthConfig,
    *,
    workspace_id: str,
    user_id: str,
) -> AuthorizationStart:
    """Create a ten-minute state; the caller commits before redirecting."""
    if db.get(Membership, (user_id, workspace_id)) is None:
        raise OAuthError("forbidden", "User is not a member of this workspace")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    now = datetime.now(timezone.utc)
    expires_at = now + STATE_LIFETIME
    db.add(YouTubeOAuthState(
        state_hash=sha256(state.encode("ascii")).hexdigest(),
        workspace_id=workspace_id,
        user_id=user_id,
        verifier_ciphertext=_encrypt(config, verifier),
        redirect_uri=config.redirect_uri,
        created_at=now,
        expires_at=expires_at,
    ))
    query = urlencode({
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": UPLOAD_SCOPE,
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return AuthorizationStart(f"{AUTHORIZATION_ENDPOINT}?{query}", state, expires_at)


def _request_tokens(client: httpx.Client, data: dict[str, str]) -> httpx.Response:
    try:
        response = client.post(TOKEN_ENDPOINT, data=data, follow_redirects=False)
    except httpx.RequestError as exc:
        raise OAuthError("google_unavailable", "Google token endpoint is unavailable", retryable=True) from exc
    if response.status_code != 200:
        raise OAuthError(
            "google_token_error",
            f"Google token endpoint returned HTTP {response.status_code}",
            retryable=response.status_code == 429 or response.status_code >= 500,
            http_status=response.status_code,
        )
    return response


def _parse_tokens(response: httpx.Response, *, fallback_scope: str = "") -> _TokenResponse:
    try:
        payload = response.json()
        access = payload["access_token"]
        expires_in = payload["expires_in"]
        token_type = payload["token_type"]
        scope = payload.get("scope", fallback_scope)
        refresh = payload.get("refresh_token")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise OAuthError("invalid_response", "Google token response is incomplete") from exc
    if (
        not isinstance(access, str) or not access
        or not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0
        or token_type != "Bearer"
        or not isinstance(scope, str) or UPLOAD_SCOPE not in scope.split()
        or (refresh is not None and (not isinstance(refresh, str) or not refresh))
    ):
        raise OAuthError("invalid_response", "Google token response does not grant YouTube upload access")
    return _TokenResponse(access, refresh, datetime.now(timezone.utc) + timedelta(seconds=expires_in), scope)


def complete_authorization(
    db: Session,
    config: GoogleOAuthConfig,
    *,
    state: str,
    code: str,
    current_user_id: str,
    client: httpx.Client,
) -> ConnectionStatus:
    """Consume state once, exchange code outside a DB transaction, save tokens."""
    if not isinstance(state, str) or not state or len(state) > 256 or not isinstance(code, str) or not code:
        raise OAuthError("invalid_state", "OAuth callback is missing valid state or code")
    digest = sha256(state.encode("utf-8")).hexdigest()
    pending = db.get(YouTubeOAuthState, digest)
    membership = db.get(Membership, (current_user_id, pending.workspace_id)) if pending is not None else None
    now = datetime.now(timezone.utc)
    if (
        pending is None or pending.user_id != current_user_id
        or pending.consumed_at is not None
        or (pending.expires_at.replace(tzinfo=timezone.utc) if pending.expires_at.tzinfo is None else pending.expires_at) <= now
        or pending.redirect_uri != config.redirect_uri
        or membership is None or membership.role != "owner"
    ):
        db.rollback()
        raise OAuthError("invalid_state", "OAuth state is expired or does not match the user")
    workspace_id = pending.workspace_id
    verifier = _decrypt(config, pending.verifier_ciphertext)
    redirect_uri = pending.redirect_uri
    changed = db.execute(
        update(YouTubeOAuthState)
        .where(
            YouTubeOAuthState.state_hash == digest,
            YouTubeOAuthState.user_id == current_user_id,
            YouTubeOAuthState.consumed_at.is_(None),
            YouTubeOAuthState.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise OAuthError("invalid_state", "OAuth state was already used")
    db.commit()

    response = _request_tokens(client, {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    tokens = _parse_tokens(response)
    connection = db.get(YouTubeConnection, workspace_id)
    if tokens.refresh_token is None:
        db.rollback()
        raise OAuthError("missing_refresh_token", "Google did not return offline authorization; reconnect with consent")
    refresh_ciphertext = _encrypt(config, tokens.refresh_token)
    now = datetime.now(timezone.utc)
    if connection is None:
        connection = YouTubeConnection(workspace_id=workspace_id)
        db.add(connection)
    connection.connected_at = now  # Fresh consent is a new channel generation.
    connection.access_token_ciphertext = _encrypt(config, tokens.access_token)
    connection.refresh_token_ciphertext = refresh_ciphertext
    connection.expires_at = tokens.expires_at
    connection.scope = tokens.scope
    connection.updated_at = now
    db.commit()
    return _status(connection)


def get_access_token(
    db: Session,
    config: GoogleOAuthConfig,
    *,
    workspace_id: str,
    client: httpx.Client,
    expected_generation: str | None = None,
) -> str:
    """Return a usable access token, refreshing it when less than a minute remains."""
    connection = db.get(YouTubeConnection, workspace_id)
    if connection is None:
        db.rollback()
        raise OAuthError("not_connected", "Workspace has no YouTube connection")
    if expected_generation is not None and connection_generation(connection) != expected_generation:
        db.rollback()
        raise OAuthError("connection_changed", "YouTube connection changed after publication was queued")
    previous_connected_at = connection.connected_at
    expires_at = connection.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at > datetime.now(timezone.utc) + EXPIRY_MARGIN:
        return _decrypt(config, connection.access_token_ciphertext)
    previous_refresh_ciphertext = connection.refresh_token_ciphertext
    scope = connection.scope
    refresh = _decrypt(config, previous_refresh_ciphertext)
    db.rollback()  # Release DB resources before calling Google.
    response = _request_tokens(client, {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    })
    tokens = _parse_tokens(response, fallback_scope=scope)
    values = {
        "access_token_ciphertext": _encrypt(config, tokens.access_token),
        "expires_at": tokens.expires_at,
        "scope": tokens.scope,
        "updated_at": datetime.now(timezone.utc),
    }
    if tokens.refresh_token is not None:
        values["refresh_token_ciphertext"] = _encrypt(config, tokens.refresh_token)
    changed = db.execute(
        update(YouTubeConnection)
        .where(
            YouTubeConnection.workspace_id == workspace_id,
            YouTubeConnection.refresh_token_ciphertext == previous_refresh_ciphertext,
            YouTubeConnection.connected_at == previous_connected_at,
        )
        .values(**values)
    )
    if changed.rowcount != 1:
        db.rollback()
        if expected_generation is not None:
            current = db.get(YouTubeConnection, workspace_id)
            if current is None or connection_generation(current) != expected_generation:
                db.rollback()
                raise OAuthError("connection_changed", "YouTube connection changed during token refresh")
        raise OAuthError("refresh_conflict", "YouTube connection changed during refresh", retryable=True)
    db.commit()
    return tokens.access_token


def disconnect(db: Session, *, workspace_id: str) -> None:
    """Delete local tokens; callers can separately revoke the grant with Google."""
    db.execute(delete(YouTubeConnection).where(YouTubeConnection.workspace_id == workspace_id))
    db.commit()
