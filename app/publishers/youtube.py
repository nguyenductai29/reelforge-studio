"""YouTube video uploads using the documented resumable protocol.

OAuth consent, token refresh, approval, and durable session storage belong to
the caller. Call ``start_private_upload`` and persist its result before calling
``upload_private_video`` when a worker must survive a process restart.

The request chooses the visibility (``private`` by default, or ``unlisted`` or
``public``) and optional tags; the ``youtube.upload`` scope allows all three.
YouTube may answer with a *more* restrictive visibility than requested (videos
uploaded through Google projects that are not verified stay private); that is
accepted and reported. A *less* restrictive answer is never accepted.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import httpx


UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024
MAX_CHUNK_SIZE = 32 * 1024 * 1024
MAX_FILE_SIZE = 256 * 1024**3
_CHUNK_GRANULARITY = 256 * 1024
_RANGE_RE = re.compile(r"bytes=0-(\d+)\Z")
_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}\Z")
PRIVACY_STATUSES = ("private", "unlisted", "public")
# How open each visibility is; an answer above the requested rank is refused.
_OPENNESS = {"private": 0, "unlisted": 1, "public": 2}
MAX_TAGS_LENGTH = 500


class YouTubeUploadError(Exception):
    """A local or Google upload failure with a stable worker-facing code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        http_status: int | None = None,
        session: "UploadSession | None" = None,
        remote_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status
        self.session = session
        self.remote_id = remote_id


@dataclass(frozen=True)
class YouTubeUploadRequest:
    file_path: Path
    title: str
    description: str = ""
    contains_synthetic_media: bool = True
    privacy_status: str = "private"
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class UploadSession:
    url: str
    file_size: int


@dataclass(frozen=True)
class UploadResult:
    video_id: str
    privacy_status: str
    upload_status: str | None


def _validate_file(request: YouTubeUploadRequest) -> int:
    path = request.file_path
    if not isinstance(path, Path) or path.suffix.lower() not in ("", ".mp4") or path.is_symlink():
        raise YouTubeUploadError("invalid_upload", "A regular MP4 file is required")
    try:
        if not path.is_file():
            raise ValueError("not a file")
        size = path.stat().st_size
        with path.open("rb") as video:
            header = video.read(12)
    except (OSError, ValueError) as exc:
        raise YouTubeUploadError("invalid_upload", "MP4 file is unavailable") from exc
    if not 12 <= size <= MAX_FILE_SIZE or header[4:8] != b"ftyp":
        raise YouTubeUploadError("invalid_upload", "MP4 file is empty or invalid")
    return size


def tags_length(tags) -> int:
    """How YouTube counts the tag limit: tags joined by commas, a tag with spaces counted with quotes."""
    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(len(tags) - 1, 0)


def _validate_request(request: YouTubeUploadRequest, access_token: str) -> int:
    if not isinstance(request, YouTubeUploadRequest):
        raise YouTubeUploadError("invalid_upload", "Invalid upload request")
    if (
        not isinstance(request.title, str)
        or not request.title.strip()
        or len(request.title) > 100
        or "\n" in request.title
        or "\r" in request.title
        or not isinstance(request.description, str)
        or len(request.description) > 5000
        or not isinstance(request.contains_synthetic_media, bool)
        or request.privacy_status not in PRIVACY_STATUSES
        or not isinstance(request.tags, tuple)
        or not all(isinstance(tag, str) and tag.strip() for tag in request.tags)
        or tags_length(request.tags) > MAX_TAGS_LENGTH
    ):
        raise YouTubeUploadError("invalid_upload", "Invalid YouTube video metadata")
    if not isinstance(access_token, str) or not access_token.strip() or any(ch.isspace() for ch in access_token):
        raise YouTubeUploadError("invalid_token", "A valid OAuth access token is required")
    return _validate_file(request)


def _validate_session_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError) as exc:
        raise YouTubeUploadError("unsafe_session_url", "Invalid YouTube upload session URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.googleapis.com"
        or parsed.path != "/upload/youtube/v3/videos"
        or parsed.fragment
        or len(url) > 4096
        or params.get("uploadType") != ["resumable"]
        or len(params.get("upload_id", [])) != 1
        or not params["upload_id"][0]
    ):
        raise YouTubeUploadError("unsafe_session_url", "Invalid YouTube upload session URL")


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    content: bytes,
    session: UploadSession | None = None,
) -> httpx.Response:
    try:
        return client.request(method, url, headers=headers, content=content, follow_redirects=False)
    except httpx.RequestError as exc:
        raise YouTubeUploadError(
            "transport_error", "YouTube upload connection failed", retryable=True, session=session
        ) from exc


def _raise_http_error(response: httpx.Response, session: UploadSession | None = None) -> None:
    status = response.status_code
    if status in (401, 403):
        code = "authorization_error"
    elif status in (404, 410) and session is not None:
        code = "expired_session"
    elif status == 429:
        code = "rate_limited"
    elif status >= 500:
        code = "youtube_unavailable"
    else:
        code = "youtube_error"
    raise YouTubeUploadError(
        code,
        f"YouTube upload returned HTTP {status}",
        retryable=status in (429,) or status >= 500,
        http_status=status,
        session=session,
    )


def _parse_result(response: httpx.Response, session: UploadSession, requested: str = "private") -> UploadResult:
    try:
        payload = response.json()
        video_id = payload["id"]
        status = payload["status"]
        privacy = status["privacyStatus"]
        upload_status = status.get("uploadStatus")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise YouTubeUploadError("invalid_response", "YouTube upload response is incomplete", session=session) from exc
    if not isinstance(video_id, str) or not _VIDEO_ID_RE.fullmatch(video_id):
        raise YouTubeUploadError("invalid_response", "YouTube upload returned an invalid video ID", session=session)
    if privacy not in _OPENNESS or _OPENNESS[privacy] > _OPENNESS.get(requested, 0):
        raise YouTubeUploadError(
            "unexpected_visibility", f"YouTube video is more visible than {requested}", session=session,
            remote_id=video_id,
        )
    if upload_status is not None and not isinstance(upload_status, str):
        raise YouTubeUploadError("invalid_response", "YouTube upload status is invalid", session=session)
    if upload_status in ("failed", "rejected", "deleted"):
        raise YouTubeUploadError("video_rejected", "YouTube rejected the uploaded video", session=session)
    return UploadResult(video_id, privacy, upload_status)


def _acknowledged_offset(response: httpx.Response, session: UploadSession) -> int:
    reported_range = response.headers.get("Range")
    if reported_range is None:
        return 0
    match = _RANGE_RE.fullmatch(reported_range)
    if match is None:
        raise YouTubeUploadError("invalid_response", "YouTube returned an invalid byte range", session=session)
    offset = int(match.group(1)) + 1
    if offset >= session.file_size:
        raise YouTubeUploadError("invalid_response", "YouTube returned an impossible byte range", session=session)
    return offset


def start_private_upload(
    request: YouTubeUploadRequest,
    access_token: str,
    *,
    client: httpx.Client,
) -> UploadSession:
    """Start an upload with the requested visibility; persist the returned session before transfer."""
    size = _validate_request(request, access_token)
    snippet = {"title": request.title.strip(), "description": request.description}
    if request.tags:
        snippet["tags"] = list(request.tags)
    metadata = {
        "snippet": snippet,
        "status": {"privacyStatus": request.privacy_status,
                   "containsSyntheticMedia": request.contains_synthetic_media},
    }
    body = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    response = _request(
        client,
        "POST",
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Content-Length": str(len(body)),
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
        content=body,
    )
    if response.status_code not in (200, 201):
        _raise_http_error(response)
    session_url = response.headers.get("Location", "")
    _validate_session_url(session_url)
    return UploadSession(session_url, size)


def _probe_offset(client: httpx.Client, session: UploadSession, access_token: str,
                  requested: str = "private") -> int | UploadResult:
    response = _request(
        client,
        "PUT",
        session.url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Length": "0",
            "Content-Range": f"bytes */{session.file_size}",
        },
        content=b"",
        session=session,
    )
    if response.status_code == 308:
        return _acknowledged_offset(response, session)
    if response.status_code in (200, 201):
        return _parse_result(response, session, requested)
    _raise_http_error(response, session)


def upload_private_video(
    request: YouTubeUploadRequest,
    access_token: str,
    *,
    client: httpx.Client,
    session: UploadSession | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> UploadResult:
    """Upload a local MP4 in bounded chunks, resuming a persisted session."""
    size = _validate_request(request, access_token)
    if (
        not isinstance(chunk_size, int)
        or chunk_size < _CHUNK_GRANULARITY
        or chunk_size > MAX_CHUNK_SIZE
        or chunk_size % _CHUNK_GRANULARITY
    ):
        raise YouTubeUploadError("invalid_upload", "Upload chunk size must be a bounded multiple of 256 KiB")
    if session is None:
        session = start_private_upload(request, access_token, client=client)
        offset = 0
    else:
        if not isinstance(session, UploadSession) or session.file_size != size:
            raise YouTubeUploadError("invalid_upload", "MP4 size changed since upload started")
        _validate_session_url(session.url)
        probed = _probe_offset(client, session, access_token, request.privacy_status)
        if isinstance(probed, UploadResult):
            return probed
        offset = probed

    retries_without_progress = 0
    try:
        with request.file_path.open("rb") as video:
            while offset < size:
                video.seek(offset)
                chunk = video.read(min(chunk_size, size - offset))
                if not chunk:
                    raise YouTubeUploadError("invalid_upload", "MP4 changed during upload", session=session)
                end = offset + len(chunk) - 1
                response = _request(
                    client,
                    "PUT",
                    session.url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                    content=chunk,
                    session=session,
                )
                if response.status_code in (200, 201):
                    return _parse_result(response, session, request.privacy_status)
                if response.status_code == 308:
                    next_offset = _acknowledged_offset(response, session)
                else:
                    _raise_http_error(response, session)
                if next_offset > end + 1:
                    raise YouTubeUploadError("invalid_response", "YouTube acknowledged unsent bytes", session=session)
                retries_without_progress = 0 if next_offset > offset else retries_without_progress + 1
                if retries_without_progress >= 3:
                    raise YouTubeUploadError(
                        "upload_stalled", "YouTube upload did not advance", retryable=True, session=session
                    )
                offset = next_offset
    except OSError as exc:
        raise YouTubeUploadError("invalid_upload", "MP4 became unavailable", session=session) from exc
    raise YouTubeUploadError("invalid_response", "YouTube did not confirm upload completion", session=session)


# The functions above now honor ``request.privacy_status``; these names say so.
start_upload = start_private_upload
upload_video = upload_private_video
