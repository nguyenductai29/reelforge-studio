"""An in-memory Google Drive v3 for tests: the part of the API app/google_drive.py uses. Nothing leaves the process.

Use it as an httpx transport::

    drive = FakeDrive()
    client = google_drive.DriveClient(settings, http_client=httpx.Client(transport=httpx.MockTransport(drive.handle)))

or serve it on a loopback port for the browser tests (``python tests/fake_drive.py --port 8021``) and point
``REELFORGE_GOOGLE_API_BASE`` at it. Tokens, folders, files, the trash and resumable upload sessions live in memory;
``fail(operation, status)`` makes the next such request fail, to test retries. ``/__test__/…`` routes (HTTP only)
let a browser test place a file in a studio's inbox and read the state back.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx

FOLDER = "application/vnd.google-apps.folder"
TOKEN = "drive-access-token-SENTINEL"


class FakeDrive:
    def __init__(self, *, root_id: str = "rootfolder01", base: str = "https://www.googleapis.com"):
        self.lock = threading.RLock()
        self.base = base.rstrip("/")
        self.root_id = root_id
        self.files: dict[str, dict] = {root_id: self._meta(root_id, "ReelForge", FOLDER, [])}
        self.content: dict[str, bytes] = {}
        self.sessions: dict[str, dict] = {}
        self.failures: dict[str, list[int]] = {}
        self.calls: list[tuple[str, str]] = []
        self.tokens = 0
        self.refuse_move = False
        self.token_requests: list[dict] = []

    # --- helpers for tests ---------------------------------------------------------------------------------------

    @staticmethod
    def _meta(file_id: str, name: str, mime: str, parents: list[str]) -> dict:
        return {"id": file_id, "name": name, "mimeType": mime, "parents": list(parents), "trashed": False,
                "createdTime": datetime.now(timezone.utc).isoformat()}

    def fail(self, operation: str, status: int, times: int = 1) -> None:
        """The next ``times`` requests of ``operation`` answer ``status`` (token, list, create, trash, delete,
        move, upload_start, upload_chunk, download, file)."""
        self.failures.setdefault(operation, []).extend([status] * times)

    def _failure(self, operation: str) -> int | None:
        queue = self.failures.get(operation)
        return queue.pop(0) if queue else None

    def folder(self, *names: str, create: bool = False) -> str | None:
        """The folder at ``root/names…`` (created when asked)."""
        current = self.root_id
        for name in names:
            found = next((item["id"] for item in self.files.values() if current in item["parents"]
                          and item["name"] == name and item["mimeType"] == FOLDER and not item["trashed"]), None)
            if found is None:
                if not create:
                    return None
                found = self._new_id()
                self.files[found] = self._meta(found, name, FOLDER, [current])
            current = found
        return current

    def add_file(self, parent: str, name: str, data: bytes, mime: str = "video/mp4") -> str:
        with self.lock:
            file_id = self._new_id()
            self.files[file_id] = self._meta(file_id, name, mime, [parent])
            self.content[file_id] = bytes(data)
            return file_id

    def children(self, parent: str, *, trashed: bool = False) -> list[dict]:
        return [item for item in self.files.values() if parent in item["parents"] and item["trashed"] == trashed]

    def live_files(self) -> list[dict]:
        """Every file (not folder) that is not in the trash."""
        return [item for item in self.files.values() if item["mimeType"] != FOLDER and not item["trashed"]]

    @staticmethod
    def _new_id() -> str:
        return "f" + uuid.uuid4().hex[:24]

    def _public(self, item: dict) -> dict:
        view = dict(item)
        if item["mimeType"] == FOLDER:
            view["capabilities"] = {"canAddChildren": True}
        else:
            data = self.content.get(item["id"], b"")
            view["size"] = str(len(data))
            view["md5Checksum"] = hashlib.md5(data).hexdigest()  # noqa: S324 - Drive's own checksum
        return view

    # --- transport -----------------------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            status, headers, body = self.route(request.method, str(request.url), dict(request.headers),
                                               request.content)
        return httpx.Response(status, headers=headers, content=body)

    @staticmethod
    def _json(status: int, value, headers: dict | None = None):
        return status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(value).encode()

    def _error(self, status: int, reason: str = ""):
        return self._json(status, {"error": {"code": status, "errors": [{"reason": reason}] if reason else []}})

    def route(self, method: str, url: str, headers: dict, body: bytes):
        parts = urlsplit(url)
        path, params = parts.path, {key: values[-1] for key, values in parse_qs(parts.query).items()}
        headers = {key.lower(): value for key, value in headers.items()}
        self.calls.append((method, path))
        if path == "/token":
            return self._token(body)
        if path.startswith("/__test__/"):
            return self._test_route(method, path, body)
        if path == "/upload/drive/v3/files" and params.get("upload_id"):
            return self._upload_chunk(params["upload_id"], headers, body)  # a session URL needs no token
        if headers.get("authorization") != f"Bearer {TOKEN}":
            return self._error(401, "authError")
        if path == "/drive/v3/about":
            return self._json(200, {"user": {"emailAddress": "drive-owner@example.com"}})
        if path == "/upload/drive/v3/files":
            if params.get("uploadType") == "resumable":
                return self._upload_start(headers, body)
            return self._multipart(headers, body)
        if path == "/drive/v3/files":
            if method == "GET":
                return self._list(params)
            return self._create(body)
        match = re.fullmatch(r"/drive/v3/files/([A-Za-z0-9_-]+)", path)
        if not match:
            return self._error(404, "notFound")
        file_id = match.group(1)
        if method == "GET" and params.get("alt") == "media":
            return self._download(file_id, headers)
        if method == "GET":
            if status := self._failure("file"):
                return self._error(status)
            item = self.files.get(file_id)
            return self._json(200, self._public(item)) if item else self._error(404, "notFound")
        if method == "PATCH":
            return self._patch(file_id, params, body)
        if method == "DELETE":
            if status := self._failure("delete"):
                return self._error(status)
            if file_id not in self.files:
                return self._error(404, "notFound")
            self._remove(file_id)
            return 204, {}, b""
        return self._error(405)

    def _token(self, body: bytes):
        form = {key: values[-1] for key, values in parse_qs(body.decode()).items()}
        self.token_requests.append({key: ("***" if key in ("client_secret", "refresh_token", "assertion") else value)
                                    for key, value in form.items()})
        if status := self._failure("token"):
            return self._json(status, {"error": "invalid_grant"})
        if form.get("grant_type") not in ("refresh_token", "urn:ietf:params:oauth:grant-type:jwt-bearer"):
            return self._json(400, {"error": "unsupported_grant_type"})
        self.tokens += 1
        return self._json(200, {"access_token": TOKEN, "expires_in": 3600, "token_type": "Bearer"})

    def _list(self, params: dict):
        if status := self._failure("list"):
            return self._error(status, "rateLimitExceeded" if status == 403 else "")
        query = params.get("q", "")
        parent = re.search(r"'([^']+)' in parents", query)
        name = re.search(r"name = '((?:[^'\\]|\\.)*)'", query)
        items = [item for item in self.files.values() if not item["trashed"]
                 and (parent is None or parent.group(1) in item["parents"])
                 and (name is None or item["name"] == name.group(1).replace("\\'", "'"))]
        if f"mimeType = '{FOLDER}'" in query:
            items = [item for item in items if item["mimeType"] == FOLDER]
        if f"mimeType != '{FOLDER}'" in query:
            items = [item for item in items if item["mimeType"] != FOLDER]
        if "mimeType contains 'video/'" in query:
            items = [item for item in items if item["mimeType"].startswith("video/")
                     or item["mimeType"] == "application/octet-stream"]
        items.sort(key=lambda item: item["name"])
        size, start = int(params.get("pageSize", "100")), int(params.get("pageToken", "0") or 0)
        page = items[start:start + size]
        result = {"files": [self._public(item) for item in page]}
        if start + size < len(items):
            result["nextPageToken"] = str(start + size)
        return self._json(200, result)

    def _create(self, body: bytes):
        if status := self._failure("create"):
            return self._error(status)
        data = json.loads(body or b"{}")
        parent = (data.get("parents") or [self.root_id])[0]
        if parent not in self.files:
            return self._error(404, "notFound")
        file_id = self._new_id()
        self.files[file_id] = self._meta(file_id, data.get("name", ""), data.get("mimeType", FOLDER), [parent])
        return self._json(200, {"id": file_id})

    def _patch(self, file_id: str, params: dict, body: bytes):
        item = self.files.get(file_id)
        data = json.loads(body or b"{}")
        if params.get("addParents"):
            if status := self._failure("move"):
                return self._error(status)
            if self.refuse_move:
                return self._error(403, "insufficientFilePermissions")
            if item is None:
                return self._error(404, "notFound")
            if params.get("removeParents") in item["parents"]:
                item["parents"].remove(params["removeParents"])
            item["parents"].append(params["addParents"])
            if data.get("name"):
                item["name"] = data["name"]
            return self._json(200, self._public(item))
        if status := self._failure("trash"):
            return self._error(status)
        if item is None:
            return self._error(404, "notFound")
        if data.get("trashed"):
            self._trash(file_id)
        return self._json(200, {"id": file_id})

    def _trash(self, file_id: str) -> None:
        self.files[file_id]["trashed"] = True
        for child in [item["id"] for item in self.files.values() if file_id in item["parents"]]:
            self._trash(child)

    def _remove(self, file_id: str) -> None:
        for child in [item["id"] for item in self.files.values() if file_id in item["parents"]]:
            self._remove(child)
        self.files.pop(file_id, None)
        self.content.pop(file_id, None)

    def _download(self, file_id: str, headers: dict):
        if status := self._failure("download"):
            return self._error(status)
        item = self.files.get(file_id)
        if item is None or item["mimeType"] == FOLDER:
            return self._error(404, "notFound")
        data = self.content.get(file_id, b"")
        ranged = re.fullmatch(r"bytes=(\d+)-", headers.get("range", ""))
        if ranged:
            start = int(ranged.group(1))
            if start >= len(data):
                return 416, {}, b""
            return 206, {"Content-Type": item["mimeType"], "Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"}, \
                data[start:]
        return 200, {"Content-Type": item["mimeType"]}, data

    def _upload_start(self, headers: dict, body: bytes):
        if status := self._failure("upload_start"):
            return self._error(status)
        data = json.loads(body or b"{}")
        parent = (data.get("parents") or [self.root_id])[0]
        if parent not in self.files:
            return self._error(404, "notFound")
        session = uuid.uuid4().hex
        self.sessions[session] = {"name": data.get("name", ""), "parent": parent,
                                  "mime": data.get("mimeType") or headers.get("x-upload-content-type", ""),
                                  "size": int(headers.get("x-upload-content-length", "0")), "data": bytearray(),
                                  "file_id": None}
        return 200, {"Location": f"{self.base}/upload/drive/v3/files?uploadType=resumable&upload_id={session}"}, b""

    def _upload_chunk(self, session_id: str, headers: dict, body: bytes):
        session = self.sessions.get(session_id)
        if session is None:
            return self._error(404, "notFound")
        size = session["size"]
        content_range = headers.get("content-range", "")
        if session["file_id"]:
            return self._json(200, self._public(self.files[session["file_id"]]))
        if content_range == f"bytes */{size}":
            received = len(session["data"])
            return 308, ({"Range": f"bytes=0-{received - 1}"} if received else {}), b""
        if status := self._failure("upload_chunk"):
            return self._error(status)
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if not match or int(match.group(1)) != len(session["data"]):
            received = len(session["data"])
            return 308, ({"Range": f"bytes=0-{received - 1}"} if received else {}), b""
        session["data"].extend(body)
        if len(session["data"]) >= size:
            file_id = self._new_id()
            self.files[file_id] = self._meta(file_id, session["name"], session["mime"], [session["parent"]])
            self.content[file_id] = bytes(session["data"])
            session["file_id"] = file_id
            return self._json(200, self._public(self.files[file_id]))
        return 308, {"Range": f"bytes=0-{len(session['data']) - 1}"}, b""

    def _multipart(self, headers: dict, body: bytes):
        boundary = re.search(r"boundary=([^;]+)", headers.get("content-type", ""))
        if not boundary:
            return self._error(400)
        parts = body.split(f"--{boundary.group(1)}".encode())
        meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].strip())
        data = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
        parent = (meta.get("parents") or [self.root_id])[0]
        file_id = self.add_file(parent, meta.get("name", ""), data, "text/plain")
        return self._json(200, self._public(self.files[file_id]))

    def _test_route(self, method: str, path: str, body: bytes):
        if path == "/__test__/state":
            return self._json(200, {"files": [self._public(item) for item in self.files.values()],
                                    "tokens": self.tokens})
        if path == "/__test__/inbox" and method == "POST":
            data = json.loads(body or b"{}")
            inbox = self.folder("inbox", data["workspace_id"], create=True)
            file_id = self.add_file(inbox, data["name"], bytes.fromhex(data["hex"]), data.get("mime", "video/mp4"))
            return self._json(200, {"id": file_id})
        return self._error(404)


def serve(port: int) -> None:
    drive = FakeDrive(base=f"http://127.0.0.1:{port}")

    class Handler(BaseHTTPRequestHandler):
        def _any(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            with drive.lock:
                status, headers, content = drive.route(self.command, f"http://127.0.0.1:{port}{self.path}",
                                                       dict(self.headers.items()), body)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _any

        def log_message(self, *_args):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve an in-memory Google Drive for the browser tests")
    parser.add_argument("--port", type=int, default=8021)
    serve(parser.parse_args().port)
