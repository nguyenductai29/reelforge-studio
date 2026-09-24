"""Facebook Page Reels upload using Meta's documented start/transfer/finish flow.

The caller owns Page OAuth, consent, token storage, and durable state. Persist the
returned ``ReelSession`` before transfer. Meta documents one binary POST for local
files, but no partial-transfer resume protocol; after an uncertain transfer result,
check ``get_reel_status`` before deciding whether to retry the whole file.

Meta collection: https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api
"""

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlencode

import httpx


API_VERSION = "v26.0"
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}"
MAX_FILE_SIZE = 1024**3  # Local safety limit, not a documented Meta limit.
_VIDEO_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_GRAPH_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class FacebookReelError(Exception):
    """Safe worker-facing error; never includes the access token or Meta's body."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class FacebookReelRequest:
    file_path: Path
    title: str
    description: str = ""


@dataclass(frozen=True)
class ReelSession:
    video_id: str
    upload_url: str
    file_size: int


@dataclass(frozen=True)
class ReelStatus:
    video_id: str
    video_status: str
    uploading_status: str | None
    processing_status: str | None
    publishing_status: str | None


@dataclass(frozen=True)
class ReelPublishResult:
    video_id: str
    accepted: bool


def _validate_token(page_access_token: str) -> None:
    if (
        not isinstance(page_access_token, str)
        or not 1 <= len(page_access_token) <= 4096
        or any(ord(character) < 33 or ord(character) > 126 for character in page_access_token)
    ):
        raise FacebookReelError("invalid_token", "A valid Page Access Token is required")


def _validate_request(request: FacebookReelRequest) -> int:
    if not isinstance(request, FacebookReelRequest):
        raise FacebookReelError("invalid_upload", "Invalid Facebook Reel request")
    if (
        not isinstance(request.title, str)
        or not 1 <= len(request.title.strip()) <= 255
        or any(ord(character) < 32 and character not in "\t" for character in request.title)
        or not isinstance(request.description, str)
        or len(request.description) > 5000
        or any(character in "\r\x00" for character in request.description)
    ):
        raise FacebookReelError("invalid_upload", "Invalid Facebook Reel metadata")
    path = request.file_path
    if not isinstance(path, Path) or path.suffix.lower() != ".mp4" or path.is_symlink():
        raise FacebookReelError("invalid_upload", "A regular MP4 file is required")
    try:
        if not path.is_file():
            raise ValueError("not a file")
        size = path.stat().st_size
        with path.open("rb") as video:
            header = video.read(12)
    except (OSError, ValueError) as exc:
        raise FacebookReelError("invalid_upload", "MP4 file is unavailable") from exc
    if not 12 <= size <= MAX_FILE_SIZE or header[4:8] != b"ftyp":
        raise FacebookReelError("invalid_upload", "MP4 file is empty, too large, or invalid")
    return size


def _validate_session(session: ReelSession, expected_size: int | None = None) -> None:
    if not isinstance(session, ReelSession) or not isinstance(session.video_id, str) or not _VIDEO_ID.fullmatch(session.video_id):
        raise FacebookReelError("invalid_session", "Invalid Facebook Reel session")
    expected_url = f"https://rupload.facebook.com/video-upload/{API_VERSION}/{session.video_id}"
    if not isinstance(session.upload_url, str) or session.upload_url != expected_url:
        raise FacebookReelError("unsafe_upload_url", "Invalid Facebook upload URL")
    if (
        not isinstance(session.file_size, int)
        or isinstance(session.file_size, bool)
        or not 12 <= session.file_size <= MAX_FILE_SIZE
        or expected_size is not None and session.file_size != expected_size
    ):
        raise FacebookReelError("invalid_session", "Facebook Reel file size changed")


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    content: bytes | object = b"",
    upload: bool = False,
) -> httpx.Response:
    try:
        response = client.request(
            method,
            url,
            headers=headers,
            content=content,
            timeout=_UPLOAD_TIMEOUT if upload else _GRAPH_TIMEOUT,
            follow_redirects=False,
        )
    except httpx.RequestError as exc:
        raise FacebookReelError("transport_error", "Facebook connection failed", retryable=True) from exc
    if 300 <= response.status_code < 400:
        raise FacebookReelError("unsafe_redirect", "Facebook returned an unexpected redirect", http_status=response.status_code)
    return response


def _raise_http_error(response: httpx.Response) -> None:
    status = response.status_code
    meta_code = None
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        meta_code = error.get("code") if isinstance(error, dict) else None
    except ValueError:
        pass
    if status in (401, 403) or meta_code in (190, 200):
        code = "authorization_error"
    elif status == 429 or meta_code in (4, 17, 32, 613):
        code = "rate_limited"
    elif status >= 500:
        code = "meta_unavailable"
    else:
        code = "meta_error"
    raise FacebookReelError(
        code,
        f"Facebook Reel request returned HTTP {status}",
        retryable=code in ("rate_limited", "meta_unavailable"),
        http_status=status,
    )


def _json_object(response: httpx.Response) -> dict:
    if response.status_code != 200:
        _raise_http_error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise FacebookReelError("invalid_response", "Facebook returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise FacebookReelError("invalid_response", "Facebook returned an invalid response")
    # Graph can return an error object with HTTP 200 in some API paths.
    if "error" in payload:
        _raise_http_error(response)
    return payload


def start_reel_upload(request: FacebookReelRequest, page_access_token: str, *, client: httpx.Client) -> ReelSession:
    """Create a Reel upload session using a Page Access Token, then persist it."""
    _validate_token(page_access_token)
    size = _validate_request(request)
    response = _request(
        client,
        "POST",
        f"{GRAPH_URL}/me/video_reels",
        headers={"Authorization": f"Bearer {page_access_token}", "Content-Type": "application/x-www-form-urlencoded"},
        content=b"upload_phase=start",
    )
    payload = _json_object(response)
    video_id = payload.get("video_id")
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
        raise FacebookReelError("invalid_response", "Facebook returned an invalid Reel ID")
    session = ReelSession(video_id, payload.get("upload_url"), size)
    _validate_session(session)
    return session


def _file_chunks(path: Path):
    with path.open("rb") as video:
        while chunk := video.read(1024 * 1024):
            yield chunk


def upload_reel_file(
    request: FacebookReelRequest,
    session: ReelSession,
    page_access_token: str,
    *,
    client: httpx.Client,
) -> bool:
    """Transfer one local MP4 in a single request; Meta has no documented chunk resume."""
    _validate_token(page_access_token)
    size = _validate_request(request)
    _validate_session(session, size)
    try:
        response = _request(
            client,
            "POST",
            session.upload_url,
            headers={
                "Authorization": f"OAuth {page_access_token}",
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
                "offset": "0",
                "file_size": str(size),
            },
            content=_file_chunks(request.file_path),
            upload=True,
        )
    except OSError as exc:
        raise FacebookReelError("invalid_upload", "MP4 file became unavailable") from exc
    if _json_object(response).get("success") is not True:
        raise FacebookReelError("invalid_response", "Facebook did not acknowledge the Reel upload")
    return True


def get_reel_status(session: ReelSession, page_access_token: str, *, client: httpx.Client) -> ReelStatus:
    """Read Meta's upload, processing, and publishing state for a persisted ID."""
    _validate_token(page_access_token)
    _validate_session(session)
    response = _request(
        client,
        "GET",
        f"{GRAPH_URL}/{session.video_id}?fields=status",
        headers={"Authorization": f"Bearer {page_access_token}"},
    )
    status = _json_object(response).get("status")
    if not isinstance(status, dict):
        raise FacebookReelError("invalid_response", "Facebook returned no Reel status")
    video_status = status.get("video_status")
    if not isinstance(video_status, str) or not video_status or len(video_status) > 100:
        raise FacebookReelError("invalid_response", "Facebook returned invalid Reel status")

    def phase(name: str) -> str | None:
        phase_data = status.get(name)
        if phase_data is None:
            return None
        phase_status = phase_data.get("status") if isinstance(phase_data, dict) else None
        if not isinstance(phase_status, str) or not phase_status or len(phase_status) > 100:
            raise FacebookReelError("invalid_response", "Facebook returned invalid Reel phase")
        return phase_status

    return ReelStatus(session.video_id, video_status, phase("uploading_phase"), phase("processing_phase"), phase("publishing_phase"))


def publish_reel(
    request: FacebookReelRequest,
    session: ReelSession,
    page_access_token: str,
    *,
    client: httpx.Client,
) -> ReelPublishResult:
    """Finish the upload and request publication; accepted does not mean live yet."""
    _validate_token(page_access_token)
    size = _validate_request(request)
    _validate_session(session, size)
    content = urlencode({
        "video_id": session.video_id,
        "upload_phase": "finish",
        "video_state": "PUBLISHED",
        "title": request.title.strip(),
        "description": request.description,
    }).encode("utf-8")
    response = _request(
        client,
        "POST",
        f"{GRAPH_URL}/me/video_reels",
        headers={"Authorization": f"Bearer {page_access_token}", "Content-Type": "application/x-www-form-urlencoded"},
        content=content,
    )
    if _json_object(response).get("success") is not True:
        raise FacebookReelError("invalid_response", "Facebook did not accept the Reel publication request")
    return ReelPublishResult(session.video_id, True)
