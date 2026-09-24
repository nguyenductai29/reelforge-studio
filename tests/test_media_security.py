"""Uploads reject spoofed media and enforce workspace storage limits."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MediaSecurityTest(unittest.TestCase):
    def test_upload_signature_and_workspace_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            program = r'''
import os
os.environ["WORKSPACE_MEDIA_QUOTA_BYTES"] = "60"
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
client = TestClient(app)
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
def upload(content): return client.post("/api/assets", files={"file":("clip.mp4", content, "video/mp4")})
assert upload(b"this is not an MP4").status_code == 415
assert client.get("/api/dashboard").json()["assets"] == []
clip = b"\x00\x00\x00\x18ftypmp42" + b"0" * 20
assert upload(clip).status_code == 201
assert upload(clip + b"0" * 20).status_code == 413
assert len(client.get("/api/dashboard").json()["assets"]) == 1
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
