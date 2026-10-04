"""TikTok Content Posting API inbox/draft MP4 uploads.

The caller must secure the user OAuth token and persist each returned UploadSession.
An inbox delivery is not a published post: the creator must finish it in TikTok.

https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video
https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide
https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status
"""

from dataclasses import dataclass, replace
import math
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import httpx


API_BASE = "https://open.tiktokapis.com"
INBOX_INIT_URL = f"{API_BASE}/v2/post/publish/inbox/video/init/"
STATUS_URL = f"{API_BASE}/v2/post/publish/status/fetch/"
MIN_CHUNK = 5_000_000
MAX_CHUNK = 64_000_000
DEFAULT_MULTI_CHUNK = 32_000_000
MAX_LAST_CHUNK = 128_000_000
MAX_VIDEO_BYTES = 100 * 1024 * 1024  # Matches ReelForge's private asset upload limit.
STREAM_BLOCK = 1024 * 1024
_PUBLISH_ID = re.compile(r"[A-Za-z0-9._~-]{1,64}\Z")
_REGIONAL_UPLOAD_HOST = re.compile(r"upload\.[a-z]{2,12}\.tiktokapis\.com\Z")


class TikTokError(Exception):
    """A local or TikTok error with a stable code for the worker."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class UploadPlan:
    video_size: int
    chunk_size: int
    total_chunk_count: int


@dataclass(frozen=True)
class UploadSession:
    publish_id: str
    upload_url: str
    video_size: int
    chunk_size: int
    total_chunk_count: int
    next_chunk_index: int = 0


@dataclass(frozen=True)
class PostStatus:
    state: Literal["uploading", "awaiting_creator", "published", "failed"]
    uploaded_bytes: int | None = None
    fail_reason: str | None = None


def _validate_publish_id(publish_id: str) -> None:
    if not isinstance(publish_id, str) or not _PUBLISH_ID.fullmatch(publish_id):
        raise TikTokError("invalid_publish_id", "Invalid TikTok publish ID")


def validate_upload_url(url: str) -> None:
    """Allow only documented TikTok HTTPS upload hosts and tokenized video paths."""
    try:
        parsed = urlsplit(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError) as exc:
        raise TikTokError("unsafe_upload_url", "Invalid TikTok upload URL") from exc
    host = parsed.hostname
    if (parsed.scheme != "https" or not host or parsed.netloc != host
            or (host != "open-upload.tiktokapis.com" and not _REGIONAL_UPLOAD_HOST.fullmatch(host))
            or parsed.path not in ("/video/", "/upload/") or parsed.fragment or len(url) > 256
            or len(params.get("upload_id", [])) != 1 or not params["upload_id"][0]
            or len(params.get("upload_token", [])) != 1 or not params["upload_token"][0]):
        raise TikTokError("unsafe_upload_url", "Invalid TikTok upload URL")


def plan_upload(video_size: int) -> UploadPlan:
    """Choose chunks within TikTok's documented 5–64 MB and final 128 MB rules."""
    if not isinstance(video_size, int) or isinstance(video_size, bool) or not 12 <= video_size <= MAX_VIDEO_BYTES:
        raise TikTokError("invalid_video", "MP4 size is outside ReelForge upload limits")
    if video_size <= MAX_CHUNK:
        return UploadPlan(video_size, video_size, 1)
    chunk_size = DEFAULT_MULTI_CHUNK
    count = video_size // chunk_size
    last_size = video_size - (count - 1) * chunk_size
    if not 2 <= count <= 1000 or not MIN_CHUNK <= chunk_size <= MAX_CHUNK or last_size > MAX_LAST_CHUNK:
        raise TikTokError("invalid_video", "Cannot split video into TikTok upload chunks")
    return UploadPlan(video_size, chunk_size, count)


def _video_size(path: Path) -> int:
    if not isinstance(path, Path) or path.suffix.lower() != ".mp4" or path.is_symlink():
        raise TikTokError("invalid_video", "A regular MP4 file is required")
    try:
        if not path.is_file():
            raise OSError("not a file")
        size = path.stat().st_size
        with path.open("rb") as video:
            header = video.read(12)
    except OSError as exc:
        raise TikTokError("invalid_video", "MP4 file is unavailable") from exc
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise TikTokError("invalid_video", "MP4 header is invalid")
    plan_upload(size)
    return size


def _validate_session(session: UploadSession) -> None:
    if not isinstance(session, UploadSession):
        raise TikTokError("invalid_session", "Invalid TikTok upload session")
    _validate_publish_id(session.publish_id)
    validate_upload_url(session.upload_url)
    expected = plan_upload(session.video_size)
    if (session.chunk_size != expected.chunk_size or session.total_chunk_count != expected.total_chunk_count
            or not isinstance(session.next_chunk_index, int) or isinstance(session.next_chunk_index, bool)
            or not 0 <= session.next_chunk_index <= session.total_chunk_count):
        raise TikTokError("invalid_session", "Invalid TikTok upload progress")


def _stream_range(path: Path, start: int, length: int):
    with path.open("rb") as video:
        video.seek(start)
        remaining = length
        while remaining:
            block = video.read(min(STREAM_BLOCK, remaining))
            if not block:
                raise TikTokError("invalid_video", "MP4 file changed during upload")
            remaining -= len(block)
            yield block


class TikTokClient:
    def __init__(self, access_token: str, *, approved_scopes: frozenset[str], granted_scopes: frozenset[str],
                 http_client: httpx.Client | None = None, timeout_seconds: float = 20.0):
        if not isinstance(access_token, str) or not access_token or any(char.isspace() for char in access_token):
            raise TikTokError("invalid_token", "A TikTok user access token is required")
        if (not isinstance(approved_scopes, (set, frozenset)) or not isinstance(granted_scopes, (set, frozenset))
                or not all(isinstance(item, str) for item in approved_scopes | granted_scopes)):
            raise TikTokError("invalid_scopes", "Approved and granted scopes must be supplied")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise TikTokError("invalid_config", "HTTP timeout must be positive")
        self.access_token = access_token
        self.approved_scopes = frozenset(approved_scopes)
        self.granted_scopes = frozenset(granted_scopes)
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client()
        self.timeout_seconds = timeout_seconds

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    def __enter__(self) -> "TikTokClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _require_upload_scope(self) -> None:
        if "video.upload" not in self.approved_scopes or "video.upload" not in self.granted_scopes:
            raise TikTokError("scope_not_authorized", "TikTok video.upload approval and user grant are required")

    def _api_request(self, url: str, payload: dict[str, Any], *, initiating: bool = False) -> dict[str, Any]:
        try:
            response = self.http_client.request(
                "POST", url,
                headers={"Authorization": f"Bearer {self.access_token}",
                         "Content-Type": "application/json; charset=UTF-8"},
                json=payload, timeout=self.timeout_seconds, follow_redirects=False,
            )
        except httpx.RequestError as exc:
            code = "submission_unknown" if initiating else "transport_error"
            raise TikTokError(code, "Could not contact TikTok", retryable=not initiating) from exc
        status = response.status_code
        if not 200 <= status < 300:
            if initiating and status >= 500:
                raise TikTokError("submission_unknown", "TikTok upload initialization outcome is unknown", http_status=status)
            if status == 429:
                code, retryable = "rate_limit_exceeded", True
            elif status in (401, 403):
                code, retryable = "auth_error", False
            elif status >= 500:
                code, retryable = "provider_unavailable", True
            else:
                code, retryable = "invalid_request", False
            raise TikTokError(code, f"TikTok returned HTTP {status}", retryable=retryable, http_status=status)
        try:
            body = response.json()
        except ValueError as exc:
            raise TikTokError("provider_response", "TikTok returned invalid JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
            raise TikTokError("provider_response", "TikTok returned an invalid response")
        error = body["error"]
        code = error.get("code")
        if code != "ok":
            if not isinstance(code, str) or not code:
                raise TikTokError("provider_response", "TikTok returned an invalid error code")
            raise TikTokError(code, str(error.get("message") or "TikTok rejected request")[:1000],
                              retryable=code in {"rate_limit_exceeded", "internal_error"}, http_status=status)
        data = body.get("data")
        if not isinstance(data, dict):
            raise TikTokError("provider_response", "TikTok returned no data")
        return data

    def init_draft_upload(self, path: Path) -> UploadSession:
        """Initialize an inbox draft; this does not publish a TikTok post."""
        self._require_upload_scope()
        plan = plan_upload(_video_size(path))
        data = self._api_request(INBOX_INIT_URL, {"source_info": {
            "source": "FILE_UPLOAD", "video_size": plan.video_size,
            "chunk_size": plan.chunk_size, "total_chunk_count": plan.total_chunk_count,
        }}, initiating=True)
        session = UploadSession(
            publish_id=data.get("publish_id"), upload_url=data.get("upload_url"),
            video_size=plan.video_size, chunk_size=plan.chunk_size,
            total_chunk_count=plan.total_chunk_count,
        )
        _validate_session(session)
        return session

    def upload_next_chunk(self, session: UploadSession, path: Path) -> UploadSession:
        """Upload one chunk and return progress that the caller can persist."""
        self._require_upload_scope()
        _validate_session(session)
        if session.next_chunk_index == session.total_chunk_count:
            return session
        if _video_size(path) != session.video_size:
            raise TikTokError("invalid_video", "MP4 size differs from upload session")
        index = session.next_chunk_index
        start = index * session.chunk_size
        length = (session.video_size - start) if index == session.total_chunk_count - 1 else session.chunk_size
        end = start + length - 1
        try:
            response = self.http_client.request(
                "PUT", session.upload_url,
                headers={"Content-Type": "video/mp4", "Content-Length": str(length),
                         "Content-Range": f"bytes {start}-{end}/{session.video_size}"},
                content=_stream_range(path, start, length), timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise TikTokError("upload_unknown", "TikTok upload progress is unknown") from exc
        expected_status = 201 if index == session.total_chunk_count - 1 else 206
        if response.status_code != expected_status:
            if response.status_code in (403, 404, 416):
                raise TikTokError("upload_session_invalid", "TikTok upload session is invalid", http_status=response.status_code)
            raise TikTokError("upload_unknown", "TikTok upload progress is unknown", http_status=response.status_code)
        return replace(session, next_chunk_index=index + 1)

    def fetch_status(self, publish_id: str) -> PostStatus:
        """Poll posting status; inbox delivery still needs creator action."""
        self._require_upload_scope()
        _validate_publish_id(publish_id)
        data = self._api_request(STATUS_URL, {"publish_id": publish_id})
        raw = data.get("status")
        states = {"PROCESSING_UPLOAD": "uploading", "SEND_TO_USER_INBOX": "awaiting_creator",
                  "PUBLISH_COMPLETE": "published", "FAILED": "failed"}
        if raw not in states:
            raise TikTokError("provider_response", "Unknown TikTok posting state")
        uploaded_bytes = data.get("uploaded_bytes")
        if uploaded_bytes is not None and (not isinstance(uploaded_bytes, int) or uploaded_bytes < 0):
            raise TikTokError("provider_response", "Invalid TikTok upload progress")
        reason = str(data.get("fail_reason") or "")[:1000] or None
        return PostStatus(states[raw], uploaded_bytes=uploaded_bytes, fail_reason=reason)
