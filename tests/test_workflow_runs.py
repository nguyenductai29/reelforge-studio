"""Workflow runs persist ordered outcomes, snapshots, retries and workspace isolation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowRunTest(unittest.TestCase):
    def test_run_and_retry_preserve_graph_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db", "frontend_origin": "http://localhost:3000", "secure_cookies": False}))
            program = r'''
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event
from app.db import engine
@event.listens_for(engine, "connect")
def fk(connection, record): connection.execute("PRAGMA foreign_keys=ON")
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
client = TestClient(app, headers={"Origin": "http://testserver"})
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
project = client.post("/api/projects", json={"title":"A", "topic":"Chủ đề"}).json()["id"]
workflow = client.post("/api/workflows", json={"name":"Demo"}).json()["id"]
graph = {"nodes":[{"id":"idea","type":"idea","x":0,"y":0}, {"id":"assets","type":"assets","x":1,"y":1}, {"id":"script","type":"script","x":2,"y":2}, {"id":"review","type":"review","x":3,"y":3}], "edges":[{"source":"idea","target":"script"},{"source":"script","target":"review"}]}
assert client.put(f"/api/workflows/{workflow}", json=graph).status_code == 200
response = client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project})
assert response.status_code == 201, response.text
run = response.json()
assert run["status"] == "blocked"
assert [(s["node_id"],s["status"]) for s in run["steps"]] == [("idea","completed"),("assets","completed"),("script","blocked"),("review","skipped")]
assert run["steps"][0]["output"]["topic"] == "Chủ đề"
assert client.get(f"/api/workflows/{workflow}/runs").json()[0]["id"] == run["id"]
recent = client.get("/api/workflow-runs").json()
assert [(r["id"], r["workflow_id"], r["project_id"]) for r in recent] == [(run["id"], workflow, project)]
dashboard = client.get("/api/dashboard").json()
assert dashboard["user"]["email"] == "a@example.com"
assert dashboard["projects"][0]["created_at"]
labeled = {"nodes":[{**graph["nodes"][0], "label":"Chủ đề chính"}], "edges":[]}
assert client.put(f"/api/workflows/{workflow}", json=labeled).json()["graph"]["nodes"][0]["label"] == "Chủ đề chính"
assert client.put(f"/api/workflows/{workflow}", json={"nodes":[{**graph["nodes"][0], "label":"x" * 81}], "edges":[]}).status_code == 422
assert client.put(f"/api/workflows/{workflow}", json=graph).status_code == 200
assert client.post("/api/ai-tools", json={"task":"script","provider":"openai","model":"example"}).status_code == 201
assert client.put(f"/api/workflows/{workflow}", json={"nodes":[graph["nodes"][0]],"edges":[]}).status_code == 200
retry = client.post(f"/api/workflow-runs/{run['id']}/retry")
assert retry.status_code == 201, retry.text
assert retry.json()["retry_of_id"] == run["id"]
assert len(retry.json()["steps"]) == 4
assert "chưa kết nối" in retry.json()["steps"][2]["detail"]
assert client.get(f"/api/workflow-runs/{run['id']}").json()["steps"][2]["detail"] == run["steps"][2]["detail"]
assert client.post(f"/api/workflows/{workflow}/runs", json={"project_id":project}).json()["status"] == "completed"
assert client.post("/api/register", json={"accept_terms": True, "email":"b@example.com","password":"long-password-123","workspace_name":"B"}).status_code == 201
assert client.get(f"/api/workflow-runs/{run['id']}").status_code == 404
assert client.post(f"/api/workflow-runs/{run['id']}/retry").status_code == 404
assert client.get(f"/api/workflows/{workflow}/runs").status_code == 404
assert client.get("/api/workflow-runs").json() == []
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target, env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
