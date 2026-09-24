"""Project input can be edited without leaking data across workspaces."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProjectEditingTest(unittest.TestCase):
    def test_edit_topic_and_workspace_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            program = r'''
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
client = TestClient(app)
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
project = client.post("/api/projects", json={"title":"Draft", "topic":"Old topic"}).json()["id"]
res = client.patch(f"/api/projects/{project}", json={"title":"  New title  ", "topic":"  New topic  "})
assert res.status_code == 200, res.text
assert res.json()["title"] == "New title"
assert res.json()["topic"] == "New topic"
assert client.get("/api/dashboard").json()["projects"][0]["topic"] == "New topic"
assert client.patch(f"/api/projects/{project}", json={"title":"   "}).status_code == 400
assert client.patch(f"/api/projects/{project}", json={"topic":""}).json()["topic"] == ""
assert client.post("/api/register", json={"email":"b@example.com", "password":"long-password-123", "workspace_name":"B"}).status_code == 201
assert client.patch(f"/api/projects/{project}", json={"topic":"Forbidden"}).status_code == 404
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
