"""Google Drive v3: the temporary storage of movie sources (app/movie_sources.py), owned by the system admin.

Authentication (Admin → System settings → Movie sources → Google Drive):

* ``oauth`` (default): the OAuth client ID and secret and a refresh token of the Google account whose Drive holds
  the sources, with the ``https://www.googleapis.com/auth/drive`` scope (the operator also places files to import
  in the inbox, which an app-only scope could not read);
* ``service_account``: a service account JSON key. The root folder must then be in a shared drive the service
  account belongs to: service accounts have no storage of their own.

Everything ReelForge stores lives under the configured root folder, found by fixed names and IDs only (never a
user's text, never a title or an email)::

    <root>/movie-sources/<workspace id>/<movie source id>/source.<ext>
    <root>/inbox/<workspace id>/      files the operator places there for that workspace to import

Files are addressed by their Drive IDs. Access tokens stay in this process's memory; the client secret, the
refresh token and the service account key are encrypted settings; a resumable upload's session URL is a
capability and is encrypted by the caller before it is stored. Nothing here logs a token or such a URL.
``REELFORGE_GOOGLE_API_BASE`` (development and tests only, loopback addresses only) points the client at a local
test server instead of Google.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app import system_config

SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_API = "https://www.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"
SOURCES_FOLDER = "movie-sources"
INBOX_FOLDER = "inbox"
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,parents,trashed,createdTime"
CHUNK = 16 * 1024 * 1024  # a multiple of 256 KiB, as resumable uploads require
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
TRANSFER_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
DEV_BASE_ENV = "REELFORGE_GOOGLE_API_BASE"
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


class DriveError(Exception):
    """A Drive failure with a stable ``code``; its message never holds a token, a key or a session URL."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status = status


@dataclass(frozen=True)
class DriveConfig:
    enabled: bool
    auth_mode: str
    root_folder_id: str
    client_id: str = field(repr=False, default="")
    client_secret: str = field(repr=False, default="")
    refresh_token: str = field(repr=False, default="")
    service_account_json: str = field(repr=False, default="")
    delete_mode: str = "trash"

    def problem(self) -> str | None:
        """Why Drive cannot be used: ``disabled``, ``no_root_folder`` or ``missing_credentials``; else None."""
        if not self.enabled:
            return "disabled"
        if not self.root_folder_id or not _ID.fullmatch(self.root_folder_id):
            return "no_root_folder"
        if self.auth_mode == "service_account":
            return None if self.service_account_json else "missing_credentials"
        return None if self.client_id and self.client_secret and self.refresh_token else "missing_credentials"


def config() -> DriveConfig:
    get = system_config.get
    return DriveConfig(enabled=bool(get("movie_sources.drive.enabled")),
                       auth_mode=str(get("movie_sources.drive.auth_mode") or "oauth"),
                       root_folder_id=str(get("movie_sources.drive.root_folder_id") or "").strip(),
                       client_id=str(get("movie_sources.drive.client_id") or "").strip(),
                       client_secret=str(get("movie_sources.drive.client_secret") or ""),
                       refresh_token=str(get("movie_sources.drive.refresh_token") or ""),
                       service_account_json=str(get("movie_sources.drive.service_account_json") or ""),
                       delete_mode="delete" if get("movie_sources.drive.delete_mode") == "delete" else "trash")


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _dev_base() -> str | None:
    """The test server address, accepted only on a loopback address (never a way to send credentials out)."""
    raw = system_config.env(DEV_BASE_ENV).strip().rstrip("/")
    if not raw:
        return None
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parts.scheme not in ("http", "https") or not loopback or parts.path not in ("", "/"):
        raise DriveError("invalid_config", f"{DEV_BASE_ENV} must be a loopback http(s) address")
    return raw


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _quoted(value: str) -> str:
    """A fixed name or an ID inside a Drive query string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient:
    """One authenticated Drive session; reuse it for a whole operation and close it afterwards."""

    def __init__(self, settings: DriveConfig | None = None, *, http_client: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time):
        self.settings = settings or config()
        if problem := self.settings.problem():
            raise DriveError("not_configured", f"Google Drive is not configured ({problem})")
        dev = _dev_base()
        self.api = dev or DRIVE_API
        self.token_url = f"{dev}/token" if dev else TOKEN_URL
        self._owns_http = http_client is None
        self.http = http_client or httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False)
        self._clock = clock
        self._token: str | None = None
        self._token_until = 0.0

    # --- lifecycle and authentication ------------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> "DriveClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _token_request(self, data: dict) -> dict:
        try:
            response = self.http.post(self.token_url, data=data, timeout=TIMEOUT)
        except httpx.TimeoutException as exc:
            raise DriveError("timeout", "Google did not answer the token request in time", retryable=True) from exc
        except httpx.RequestError as exc:
            raise DriveError("network_error", "Could not reach Google to authenticate", retryable=True) from exc
        if response.status_code in (400, 401, 403):
            raise DriveError("auth_failed", "Google refused the Drive credentials", status=response.status_code)
        if response.status_code >= 500 or response.status_code == 429:
            raise DriveError("server_error", f"Google answered HTTP {response.status_code} to the token request",
                             retryable=True, status=response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise DriveError("invalid_response", "Google returned an invalid token response") from exc
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            raise DriveError("invalid_response", "Google returned no access token")
        return body

    def _assertion(self) -> str:
        """A signed JWT for a service account (RS256), valid one hour."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        try:
            info = json.loads(self.settings.service_account_json)
            email, pem, key_id = info["client_email"], info["private_key"], info.get("private_key_id")
            key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        except (ValueError, KeyError, TypeError) as exc:
            raise DriveError("auth_failed", "The service account key is not valid") from exc
        now = int(self._clock())
        header = {"alg": "RS256", "typ": "JWT", **({"kid": key_id} if isinstance(key_id, str) else {})}
        claims = {"iss": email, "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600}
        signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
        try:
            signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        except (TypeError, ValueError) as exc:
            raise DriveError("auth_failed", "The service account key cannot sign") from exc
        return f"{signing_input}.{_b64url(signature)}"

    def token(self) -> str:
        if self._token and self._clock() < self._token_until:
            return self._token
        if self.settings.auth_mode == "service_account":
            body = self._token_request({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                        "assertion": self._assertion()})
        else:
            body = self._token_request({"grant_type": "refresh_token", "client_id": self.settings.client_id,
                                        "client_secret": self.settings.client_secret,
                                        "refresh_token": self.settings.refresh_token})
        lifetime = body.get("expires_in") if isinstance(body.get("expires_in"), int) else 3600
        self._token, self._token_until = body["access_token"], self._clock() + max(60, lifetime - 120)
        return self._token

    def _headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {self.token()}", **(extra or {})}

    # --- requests --------------------------------------------------------------------------------------------

    @staticmethod
    def _failure(response: httpx.Response, action: str) -> DriveError:
        status = response.status_code
        reason = ""
        try:
            body = response.json()
            errors = (body.get("error") or {}).get("errors") or []
            reason = str((errors[0] or {}).get("reason") or "") if errors else ""
        except (ValueError, AttributeError, TypeError, IndexError):
            pass
        if status == 401:
            return DriveError("auth_failed", f"Google Drive refused the credentials ({action})", status=status)
        if status == 404:
            return DriveError("not_found", f"Google Drive has no such file or folder ({action})", status=status)
        if status == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded"):
            return DriveError("rate_limited", f"Google Drive is rate limiting ({action})", retryable=True,
                              status=status)
        if reason in ("storageQuotaExceeded", "quotaExceeded", "teamDriveFileLimitExceeded"):
            return DriveError("quota_exceeded", f"Google Drive is full ({action})", status=status)
        if status == 403:
            return DriveError("forbidden", f"Google Drive refused the {action}", status=status)
        if status >= 500:
            return DriveError("server_error", f"Google Drive answered HTTP {status} ({action})", retryable=True,
                              status=status)
        return DriveError("invalid_request", f"Google Drive answered HTTP {status} ({action})", status=status)

    def _send(self, method: str, url: str, action: str, *, timeout=TIMEOUT, headers: dict | None = None,
              **kwargs) -> httpx.Response:
        for attempt in (1, 2):
            try:
                response = self.http.request(method, url, headers=self._headers(headers), timeout=timeout, **kwargs)
            except httpx.TimeoutException as exc:
                raise DriveError("timeout", f"Google Drive did not answer in time ({action})", retryable=True) from exc
            except httpx.RequestError as exc:
                raise DriveError("network_error", f"Could not reach Google Drive ({action})", retryable=True) from exc
            if response.status_code == 401 and attempt == 1:
                self._token = None  # an access token revoked early: one fresh token, once
                continue
            return response
        return response

    def _json(self, method: str, path: str, action: str, *, ok=(200,), **kwargs) -> dict:
        response = self._send(method, f"{self.api}{path}", action, **kwargs)
        if response.status_code not in ok:
            raise self._failure(response, action)
        try:
            body = response.json() if response.content else {}
        except ValueError as exc:
            raise DriveError("invalid_response", f"Google Drive returned invalid JSON ({action})") from exc
        if not isinstance(body, dict):
            raise DriveError("invalid_response", f"Google Drive returned an invalid response ({action})")
        return body

    # --- files and folders -----------------------------------------------------------------------------------

    def file(self, file_id: str, fields: str = FILE_FIELDS) -> dict:
        if not valid_id(file_id):
            raise DriveError("invalid_request", "Invalid Drive file ID")
        return self._json("GET", f"/drive/v3/files/{file_id}", "file lookup",
                          params={"fields": fields, "supportsAllDrives": "true"})

    def children(self, folder_id: str, *, name: str | None = None, folders: bool | None = None,
                 videos: bool = False, page_token: str | None = None, page_size: int = 100) -> dict:
        """One page of a folder's children (not in the trash): ``{"files": [...], "nextPageToken": ...}``."""
        if not valid_id(folder_id):
            raise DriveError("invalid_request", "Invalid Drive folder ID")
        clauses = [f"'{_quoted(folder_id)}' in parents", "trashed = false"]
        if name is not None:
            clauses.append(f"name = '{_quoted(name)}'")
        if folders is True:
            clauses.append(f"mimeType = '{FOLDER_MIME}'")
        elif folders is False:
            clauses.append(f"mimeType != '{FOLDER_MIME}'")
        if videos:
            clauses.append("(mimeType contains 'video/' or mimeType = 'application/octet-stream')")
        params = {"q": " and ".join(clauses), "fields": f"nextPageToken,files({FILE_FIELDS})",
                  "pageSize": str(max(1, min(page_size, 1000))), "orderBy": "name",
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if page_token:
            if len(page_token) > 1000:
                raise DriveError("invalid_request", "Invalid page token")
            params["pageToken"] = page_token
        body = self._json("GET", "/drive/v3/files", "folder listing", params=params)
        files = [item for item in body.get("files") or [] if isinstance(item, dict) and valid_id(item.get("id"))]
        return {"files": files, "nextPageToken": body.get("nextPageToken")
                if isinstance(body.get("nextPageToken"), str) else None}

    def create_folder(self, parent_id: str, name: str) -> str:
        body = self._json("POST", "/drive/v3/files", "folder creation", ok=(200, 201),
                          params={"fields": "id", "supportsAllDrives": "true"},
                          json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]})
        if not valid_id(body.get("id")):
            raise DriveError("invalid_response", "Google Drive returned no folder ID")
        return body["id"]

    def ensure_folder(self, parent_id: str, name: str) -> str:
        """The child folder with this fixed name, created when it does not exist."""
        found = self.children(parent_id, name=name, folders=True, page_size=2)["files"]
        return found[0]["id"] if found else self.create_folder(parent_id, name)

    def source_folder(self, workspace_id: str, source_id: str) -> str:
        sources = self.ensure_folder(self.settings.root_folder_id, SOURCES_FOLDER)
        return self.ensure_folder(self.ensure_folder(sources, workspace_id), source_id)

    def inbox_folder(self, workspace_id: str, *, create: bool = False) -> str | None:
        """This workspace's inbox (``<root>/inbox/<workspace id>``), or None when it does not exist yet."""
        if create:
            return self.ensure_folder(self.ensure_folder(self.settings.root_folder_id, INBOX_FOLDER), workspace_id)
        inbox = self.children(self.settings.root_folder_id, name=INBOX_FOLDER, folders=True, page_size=2)["files"]
        if not inbox:
            return None
        found = self.children(inbox[0]["id"], name=workspace_id, folders=True, page_size=2)["files"]
        return found[0]["id"] if found else None

    def move(self, file_id: str, new_parent: str, old_parent: str, name: str) -> dict:
        """Move a file into ``new_parent`` under a fixed ``name`` (an inbox import keeps its bytes in Drive)."""
        if not all(valid_id(value) for value in (file_id, new_parent, old_parent)):
            raise DriveError("invalid_request", "Invalid Drive ID")
        return self._json("PATCH", f"/drive/v3/files/{file_id}", "move",
                          params={"addParents": new_parent, "removeParents": old_parent, "supportsAllDrives": "true",
                                  "fields": FILE_FIELDS}, json={"name": name})

    def trash(self, file_id: str) -> bool:
        """Move to the trash; False when the file is already gone (deleting twice is harmless)."""
        if not valid_id(file_id):
            raise DriveError("invalid_request", "Invalid Drive file ID")
        response = self._send("PATCH", f"{self.api}/drive/v3/files/{file_id}", "trash",
                              params={"supportsAllDrives": "true", "fields": "id"}, json={"trashed": True})
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise self._failure(response, "trash")
        return True

    def delete(self, file_id: str) -> bool:
        """Delete permanently; False when the file is already gone."""
        if not valid_id(file_id):
            raise DriveError("invalid_request", "Invalid Drive file ID")
        response = self._send("DELETE", f"{self.api}/drive/v3/files/{file_id}", "deletion",
                              params={"supportsAllDrives": "true"})
        if response.status_code == 404:
            return False
        if response.status_code not in (200, 204):
            raise self._failure(response, "deletion")
        return True

    def remove(self, file_id: str) -> bool:
        """Trash or delete, as Admin → System settings → Movie sources says (trash by default)."""
        return self.delete(file_id) if self.settings.delete_mode == "delete" else self.trash(file_id)

    # --- uploads ---------------------------------------------------------------------------------------------

    def _session_ok(self, url: str) -> bool:
        """A session URL must stay on Google (or the test server): an upload never goes anywhere else."""
        parts = urlsplit(url)
        expected = urlsplit(self.api)
        return parts.scheme == expected.scheme and parts.hostname == expected.hostname and parts.port == expected.port

    def start_upload(self, folder_id: str, name: str, mime_type: str, size: int) -> str:
        """A resumable upload session for one file (its URL is a capability: encrypt it before storing it)."""
        response = self._send("POST", f"{self.api}/upload/drive/v3/files", "upload start",
                              params={"uploadType": "resumable", "supportsAllDrives": "true", "fields": FILE_FIELDS},
                              headers={"X-Upload-Content-Type": mime_type, "X-Upload-Content-Length": str(size)},
                              json={"name": name, "parents": [folder_id], "mimeType": mime_type})
        if response.status_code != 200:
            raise self._failure(response, "upload start")
        session = response.headers.get("Location", "")
        if not session or not self._session_ok(session):
            raise DriveError("invalid_response", "Google Drive returned no usable upload session")
        return session

    def _put(self, session: str, **kwargs) -> httpx.Response:
        try:
            return self.http.put(session, timeout=TRANSFER_TIMEOUT, **kwargs)
        except httpx.TimeoutException as exc:
            raise DriveError("timeout", "Google Drive did not answer during the upload", retryable=True) from exc
        except httpx.RequestError as exc:
            raise DriveError("network_error", "The upload to Google Drive was interrupted", retryable=True) from exc

    def upload_offset(self, session: str, size: int) -> int | dict:
        """How many bytes the session already holds, or the finished file's metadata."""
        response = self._put(session, headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"})
        if response.status_code in (200, 201):
            try:
                return response.json()
            except ValueError as exc:
                raise DriveError("invalid_response", "Google Drive returned invalid upload metadata") from exc
        if response.status_code == 308:
            received = response.headers.get("Range", "")
            return int(received.rsplit("-", 1)[1]) + 1 if received.startswith("bytes=") else 0
        if response.status_code in (404, 410):
            raise DriveError("session_expired", "The upload session expired", retryable=True)
        raise self._failure(response, "upload status")

    def upload(self, session: str, path: Path, size: int, *, offset: int = 0,
               progress: Callable[[int], None] | None = None) -> dict:
        """Send ``path`` from ``offset`` in chunks; returns the new file's metadata."""
        stalled = 0
        with open(path, "rb") as handle:
            while True:
                handle.seek(offset)
                chunk = handle.read(CHUNK)
                if not chunk and offset < size:
                    raise DriveError("upload_failed", "The local file is shorter than expected")
                end = offset + len(chunk) - 1
                response = self._put(session, content=chunk,
                                     headers={"Content-Length": str(len(chunk)),
                                              "Content-Range": f"bytes {offset}-{end}/{size}"})
                if response.status_code in (200, 201):
                    if progress:
                        progress(size)
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise DriveError("invalid_response", "Google Drive returned invalid upload metadata") from exc
                if response.status_code != 308:
                    if response.status_code in (404, 410):
                        raise DriveError("session_expired", "The upload session expired", retryable=True)
                    raise self._failure(response, "upload")
                received = response.headers.get("Range", "")
                advanced = int(received.rsplit("-", 1)[1]) + 1 if received.startswith("bytes=") else offset
                stalled = stalled + 1 if advanced <= offset else 0
                if stalled >= 3:
                    raise DriveError("upload_failed", "Google Drive did not accept the upload's bytes", retryable=True)
                offset = advanced
                if progress:
                    progress(offset)

    def upload_small(self, folder_id: str, name: str, data: bytes, mime_type: str = "text/plain") -> dict:
        """One multipart request for a tiny file (the connection test)."""
        boundary = f"reelforge{secrets.token_hex(8)}"
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps({'name': name, 'parents': [folder_id]})}\r\n--{boundary}\r\n"
                f"Content-Type: {mime_type}\r\n\r\n").encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode()
        return self._json("POST", "/upload/drive/v3/files", "test upload", ok=(200, 201),
                          params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": FILE_FIELDS},
                          headers={"Content-Type": f"multipart/related; boundary={boundary}"}, content=body)

    # --- downloads -------------------------------------------------------------------------------------------

    def download(self, file_id: str, target: Path, *, limit: int, on_chunk: Callable[[bytes], None] | None = None,
                 progress: Callable[[int], None] | None = None) -> int:
        """Write the file to ``target``, resuming after its current size; returns the file's size.

        ``on_chunk`` receives only the bytes written by this call: a caller that resumes hashes the existing part
        of ``target`` first."""
        if not valid_id(file_id):
            raise DriveError("invalid_request", "Invalid Drive file ID")
        start = target.stat().st_size if target.exists() else 0
        extra = {"Range": f"bytes={start}-"} if start else {}
        try:
            with self.http.stream("GET", f"{self.api}/drive/v3/files/{file_id}",
                                  params={"alt": "media", "supportsAllDrives": "true"},
                                  headers=self._headers(extra), timeout=TRANSFER_TIMEOUT) as response:
                if response.status_code == 416 and start:
                    return start  # nothing left to fetch
                if response.status_code not in (200, 206):
                    response.read()
                    raise self._failure(response, "download")
                resumed = response.status_code == 206
                written = start if resumed else 0
                with open(target, "ab" if resumed else "wb") as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        written += len(chunk)
                        if written > limit:
                            raise DriveError("too_large", "The Drive file is larger than the movie source limit")
                        handle.write(chunk)
                        if on_chunk:
                            on_chunk(chunk)
                        if progress:
                            progress(written)
                return written
        except httpx.TimeoutException as exc:
            raise DriveError("timeout", "Google Drive stopped sending the file", retryable=True) from exc
        except httpx.RequestError as exc:
            raise DriveError("network_error", "The download from Google Drive was interrupted",
                             retryable=True) from exc

    def about(self) -> dict:
        """The account Drive acts as (its email address, for the admin's check)."""
        return self._json("GET", "/drive/v3/about", "account lookup", params={"fields": "user(emailAddress)"})


def file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - compared with Drive's own md5Checksum, not used for security
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_connection(settings: DriveConfig | None = None, *, http_client: httpx.Client | None = None) -> dict:
    """What Admin → System settings → Movie sources → Test connection reports, step by step.

    Credentials usable, the root folder reachable and writable, a tiny file uploaded, read back and deleted
    permanently (nothing is left behind). Never returns a credential, a token or a file's content."""
    settings = settings or config()
    checks: list[dict[str, Any]] = []

    def record(key: str, status: str, code: str | None = None, **facts) -> None:
        checks.append({"key": key, "status": status, **({"code": code} if code else {}), **facts})

    problem = settings.problem()
    if problem:
        record("configuration", "error", problem)
        return {"status": "error", "checks": checks}
    record("configuration", "ok")
    try:
        client = DriveClient(settings, http_client=http_client)
    except DriveError as exc:
        record("credentials", "error", exc.code)
        return {"status": "error", "checks": checks}
    test_id = None
    try:
        try:
            client.token()
            account = client.about().get("user") or {}
            record("credentials", "ok", account=str(account.get("emailAddress") or "")[:254] or None)
        except DriveError as exc:
            record("credentials", "error", exc.code)
            return {"status": "error", "checks": checks}
        try:
            root = client.file(settings.root_folder_id, "id,name,mimeType,trashed,capabilities(canAddChildren)")
            usable = root.get("mimeType") == FOLDER_MIME and not root.get("trashed")
            writable = bool((root.get("capabilities") or {}).get("canAddChildren", True))
            record("root_folder", "ok" if usable and writable else "error",
                   None if usable and writable else ("not_a_folder" if not usable else "not_writable"))
            if not (usable and writable):
                return {"status": "error", "checks": checks}
        except DriveError as exc:
            record("root_folder", "error", exc.code)
            return {"status": "error", "checks": checks}
        payload = f"ReelForge connection test {secrets.token_hex(8)}\n".encode()
        try:
            created = client.upload_small(settings.root_folder_id, f"reelforge-connection-test-{secrets.token_hex(4)}.txt",
                                          payload)
            test_id = created.get("id") if valid_id(created.get("id")) else None
            read = client.file(test_id) if test_id else {}
            same = str(read.get("size")) == str(len(payload)) and read.get("md5Checksum") in (
                None, hashlib.md5(payload).hexdigest())  # noqa: S324
            record("upload", "ok" if test_id and same else "error", None if test_id and same else "verify_failed")
        except DriveError as exc:
            record("upload", "error", exc.code)
        if test_id:
            try:
                client.delete(test_id)
                test_id = None
                record("delete", "ok")
            except DriveError as exc:
                record("delete", "error", exc.code)
                try:
                    client.trash(test_id)  # at least out of sight; the admin is told it is in the trash
                    record("cleanup", "warning", "left_in_trash")
                except DriveError:
                    record("cleanup", "error", "test_file_left")
    finally:
        client.close()
    status = "ok" if all(check["status"] == "ok" for check in checks) else "error"
    return {"status": status, "checks": checks}
