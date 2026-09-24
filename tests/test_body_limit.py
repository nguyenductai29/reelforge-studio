"""The upload body limit runs before multipart parsing can spool to disk."""

import asyncio
from collections import deque
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient

from app.body_limit import RequestBodyLimitMiddleware


class BodyLimitTests(unittest.TestCase):
    @staticmethod
    def request_scope(path="/api/assets", content_length=None):
        headers = []
        if content_length is not None:
            headers.append((b"content-length", str(content_length).encode()))
        return {"type": "http", "method": "POST", "path": path, "headers": headers}

    @staticmethod
    def run_asgi(app, scope, chunks):
        events = []
        received = []
        pending = deque(chunks)

        async def receive():
            received.append(True)
            if not pending:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": pending.popleft(), "more_body": bool(pending)}

        async def send(event):
            events.append(event)

        asyncio.run(app(scope, receive, send))
        return events, len(received)

    def test_oversized_content_length_rejected_without_reading_or_running_app(self):
        invoked = []

        async def downstream(scope, receive, send):
            invoked.append(True)

        app = RequestBodyLimitMiddleware(downstream, max_body_bytes=5)
        events, reads = self.run_asgi(app, self.request_scope(content_length=6), [b"abcdef"])
        self.assertEqual((reads, invoked), (0, []))
        self.assertEqual([event["type"] for event in events], ["http.response.start", "http.response.body"])
        self.assertEqual(events[0]["status"], 413)
        self.assertEqual(json.loads(events[1]["body"]), {"detail": "File exceeds 100 MB"})

    def test_missing_content_length_rejects_before_running_app(self):
        accepted = []

        async def downstream(scope, receive, send):
            while True:
                event = await receive()
                accepted.append(event["body"])
                if not event.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 201, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        app = RequestBodyLimitMiddleware(downstream, max_body_bytes=5)
        events, reads = self.run_asgi(app, self.request_scope(), [b"abc", b"def"])
        self.assertEqual(reads, 2)
        self.assertEqual(accepted, [])
        self.assertEqual([event["type"] for event in events], ["http.response.start", "http.response.body"])
        self.assertEqual(events[0]["status"], 413)

    def test_underreported_content_length_is_still_capped(self):
        seen = []

        async def downstream(scope, receive, send):
            while True:
                event = await receive()
                seen.append(event["body"])
                if not event.get("more_body"):
                    break

        app = RequestBodyLimitMiddleware(downstream, max_body_bytes=5)
        events, _ = self.run_asgi(app, self.request_scope(content_length=1), [b"abc", b"def"])
        self.assertEqual(seen, [])
        self.assertEqual(events[0]["status"], 413)

    def test_other_routes_are_unchanged(self):
        async def downstream(scope, receive, send):
            body = bytearray()
            while True:
                event = await receive()
                body.extend(event["body"])
                if not event.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": bytes(body)})

        app = RequestBodyLimitMiddleware(downstream, max_body_bytes=5)
        events, reads = self.run_asgi(app, self.request_scope(path="/api/other", content_length=6), [b"abcdef"])
        self.assertEqual(reads, 1)
        self.assertEqual(events[0]["status"], 200)
        self.assertEqual(events[1]["body"], b"abcdef")

    def test_exact_limit_passes_to_app(self):
        accepted = []

        async def downstream(scope, receive, send):
            accepted.append((await receive())["body"])
            await send({"type": "http.response.start", "status": 201, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        app = RequestBodyLimitMiddleware(downstream, max_body_bytes=5)
        events, reads = self.run_asgi(app, self.request_scope(content_length=5), [b"abcde"])
        self.assertEqual((accepted, reads), ([b"abcde"], 1))
        self.assertEqual(events[0]["status"], 201)

    def test_oversized_body_does_not_start_downstream_response(self):
        events = []
        invoked = []

        async def downstream(scope, receive, send):
            invoked.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await receive()

        app = RequestBodyLimitMiddleware(downstream, max_body_bytes=5)

        async def receive():
            return {"type": "http.request", "body": b"abcdef", "more_body": False}

        async def send(event):
            events.append(event)

        asyncio.run(app(self.request_scope(), receive, send))
        self.assertEqual(invoked, [])
        self.assertEqual([event["type"] for event in events], ["http.response.start", "http.response.body"])
        self.assertEqual(events[0]["status"], 413)

    def test_fastapi_multipart_rejected_before_endpoint(self):
        api = FastAPI()
        called = []

        @api.post("/api/assets")
        async def upload(file: UploadFile = File(...)):
            called.append(True)
            return {"filename": file.filename}

        api.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=100)
        with TestClient(api) as client:
            response = client.post("/api/assets", files={"file": ("clip.mp4", b"x" * 200, "video/mp4")})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"detail": "File exceeds 100 MB"})
        self.assertEqual(called, [])

    def test_fastapi_multipart_chunked_body_rejected_before_endpoint(self):
        api = FastAPI()
        called = []

        @api.post("/api/assets")
        async def upload(file: UploadFile = File(...)):
            called.append(True)
            return {"filename": file.filename}

        api.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=100)
        boundary = "test-boundary"
        body = (
            b"--test-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
            b"Content-Type: video/mp4\r\n\r\n"
            + b"x" * 200
            + b"\r\n--test-boundary--\r\n"
        )
        with TestClient(api) as client:
            response = client.post(
                "/api/assets",
                content=iter((body[:70], body[70:])),
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"detail": "File exceeds 100 MB"})
        self.assertEqual(called, [])

    def test_real_asset_route_rejects_unauthorized_requests_without_reading_body(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(root / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(root / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({
                "database_url": f"sqlite:///{target}/instance/test.db",
            }))
            program = r'''
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import Session
from app.models import Subscription, Workspace

command.upgrade(Config("alembic.ini"), "head")
from app.main import app

def unread_body():
    raise AssertionError("The request body was consumed before authorization")
    yield b"never sent"

def upload_without_body_read(client, headers=None):
    return client.post("/api/assets", content=unread_body(),
        headers={"content-type": "multipart/form-data; boundary=test", **(headers or {})})

with TestClient(app) as client:
    response = upload_without_body_read(client)
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Please sign in"}

    response = client.post("/api/setup", json={
        "email": "owner@example.com", "password": "long-password-123",
    })
    assert response.status_code == 200, response.text
    response = upload_without_body_read(client, {"origin": "https://attacker.example"})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Invalid origin"}

    with Session.begin() as db:
        workspace = db.scalar(select(Workspace))
        db.get(Subscription, workspace.id).status = "paused"
    response = upload_without_body_read(client)
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Workspace subscription is inactive"}
'''
            result = subprocess.run([sys.executable, "-c", program], cwd=target,
                env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
