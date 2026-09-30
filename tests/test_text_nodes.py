"""Text workflow nodes, the text worker and their credit accounting, on an isolated SQLite database."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app import jobs, text_worker
from app.models import (AITool, Base, CreditAccount, CreditLedger, Project, UsageEvent, User, Workflow, WorkflowJob,
                        WorkflowRun, WorkflowRunStep, Workspace, WorkspaceSetting)
from app.providers.text import TextProviderError, TextResult, TextUsage
from app.workflow import (ExecutionContext, NodeExecutionResult, NodeHandler, RunRequestError, WorkflowExecutor,
                          build_default_registry, default_registry)
from app.workflow.nodes.text import MISSING_INPUT_DETAIL, MISSING_TOOL_DETAIL, QUEUED_DETAIL

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"OPENAI_API_KEY": "sk-test", "TEXT_CREDITS_PER_GENERATION": "1"}


def chain(*nodes):
    """Nodes as (id, type[, config]) joined left to right."""
    graph_nodes = []
    for index, spec in enumerate(nodes):
        node = {"id": spec[0], "type": spec[1], "x": index * 100, "y": 0}
        if len(spec) > 2:
            node["config"] = spec[2]
        graph_nodes.append(node)
    return {"nodes": graph_nodes,
            "edges": [{"source": a[0], "target": b[0]} for a, b in zip(nodes, nodes[1:])]}


class SourceHandler(NodeHandler):
    """Stands in for any upstream node that produced text."""

    node_type = "source"

    def execute(self, context, node, inputs):
        return NodeExecutionResult.completed("source", {"text": "Nội dung gốc về rừng đêm."})


def with_source():
    registry = build_default_registry()
    registry.register(SourceHandler())
    return registry


class FakeProvider:
    """Provider factory and provider in one: replies with queued outputs and records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, provider_name):
        self.provider_name = provider_name
        return self

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return TextResult(text=reply, usage=TextUsage.of(40, 60), provider=self.provider_name,
                          model=kwargs["model"], raw_metadata={"finish_reason": "stop"})

    def close(self):
        pass


@patch.dict(os.environ, KEYS)
class TextNodeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'text.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="text@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Text", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Rừng", topic="Rừng đêm"))
            db.add(Project(id="empty", workspace_id="space-1", title="", topic=""))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.add(CreditAccount(workspace_id="space-1", balance=0))
            db.add(WorkspaceSetting(workspace_id="space-1", key="default_language", value='"vi"'))
            db.add(AITool(id="tool-1", workspace_id="space-1", task="script", provider="openai", model="gpt-4.1-mini"))

    # Helpers ----------------------------------------------------------------

    def fund(self, credits):
        with self.Session.begin() as db:
            db.get(CreditAccount, "space-1").balance += credits

    def balance(self):
        with self.Session() as db:
            return db.get(CreditAccount, "space-1").balance

    def start(self, graph, *, registry=None, project_id="project-1"):
        with self.Session.begin() as db:
            run = WorkflowRun(id=str(uuid4()), workspace_id="space-1", workflow_id="workflow-1", project_id=project_id,
                              graph_snapshot=json.dumps(graph), status="running", created_at=datetime.now(timezone.utc))
            db.add(run)
            db.flush()
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), project=db.get(Project, project_id),
                                       run=run, graph=graph)
            WorkflowExecutor(registry).start_run(context)
            return run.id

    def work(self, provider):
        return text_worker.run_one(provider_factory=provider, session_factory=self.Session, retry_seconds=0)

    def steps(self, run_id):
        with self.Session() as db:
            return {step.node_id: step for step in db.scalars(
                select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id).order_by(WorkflowRunStep.position))}

    def output(self, run_id, node_id):
        return json.loads(self.steps(run_id)[node_id].output)

    def run_status(self, run_id):
        with self.Session() as db:
            return db.get(WorkflowRun, run_id).status

    def job_payload(self, run_id, node_id):
        with self.Session() as db:
            step = self.steps(run_id)[node_id]
            return db.scalar(select(WorkflowJob).where(WorkflowJob.step_id == step.id)).payload

    # Nodes ------------------------------------------------------------------

    def test_ai_writer_uses_its_config_and_the_worker_completes_it(self):
        self.fund(5)
        config = {"prompt": "Giới thiệu rừng đêm", "language": "en", "tone": "cinematic", "platform": "youtube",
                  "duration": 60}
        run_id = self.start(chain(("idea", "idea"), ("writer", "ai_writer", config)))
        steps = self.steps(run_id)
        self.assertEqual((steps["writer"].status, steps["writer"].detail), ("queued", QUEUED_DETAIL))
        self.assertEqual(self.run_status(run_id), "running")
        self.assertEqual(self.balance(), 4)
        payload = self.job_payload(run_id, "writer")
        self.assertEqual((payload["provider"], payload["model"], payload["language"], payload["credits"]),
                         ("openai", "gpt-4.1-mini", "en", 1))
        for expected in ("in English", "Brief: Giới thiệu rừng đêm", "Tone: cinematic.", "Platform: youtube.",
                         "about 60 seconds", "150 words"):
            self.assertIn(expected, payload["prompt"])

        provider = FakeProvider("A night in the forest…")
        self.assertTrue(self.work(provider))
        self.assertEqual(provider.calls[0]["model"], "gpt-4.1-mini")
        self.assertEqual(self.output(run_id, "writer"),
                         {"text": "A night in the forest…", "provider": "openai", "model": "gpt-4.1-mini",
                          "usage": {"input_tokens": 40, "output_tokens": 60, "total_tokens": 100}, "language": "en"})
        self.assertEqual(self.steps(run_id)["writer"].status, "completed")
        self.assertEqual(self.run_status(run_id), "completed")
        with self.Session() as db:
            event_row = db.scalar(select(UsageEvent))
            self.assertEqual((event_row.tool, event_row.units, event_row.credits), ("openai/text", 100, 1))
        self.assertEqual(self.balance(), 4)
        self.assertFalse(self.work(provider))

    def test_upstream_text_flows_through_writer_summarize_and_translate(self):
        self.fund(3)
        run_id = self.start(chain(("idea", "idea"), ("writer", "ai_writer"), ("summary", "summarize"),
                                  ("japanese", "translate", {"target_language": "ja"})))
        self.assertEqual([s.status for s in self.steps(run_id).values()], ["completed", "queued", "skipped", "skipped"])
        self.assertIn("Brief: Rừng đêm", self.job_payload(run_id, "writer")["prompt"])
        provider = FakeProvider("BÀI VIẾT ĐẦY ĐỦ", "TÓM TẮT", "要約")
        self.work(provider)
        self.assertEqual(self.steps(run_id)["summary"].status, "queued")
        summary_prompt = self.job_payload(run_id, "summary")["prompt"]
        self.assertIn("BÀI VIẾT ĐẦY ĐỦ", summary_prompt)
        self.assertIn("in Vietnamese", summary_prompt)
        self.work(provider)
        translate = self.job_payload(run_id, "japanese")
        self.assertIn("TÓM TẮT", translate["prompt"])
        self.assertIn("into Japanese", translate["prompt"])
        self.assertEqual(translate["language"], "ja")
        self.work(provider)
        self.assertEqual(self.output(run_id, "japanese")["text"], "要約")
        self.assertEqual(self.output(run_id, "writer")["text"], "BÀI VIẾT ĐẦY ĐỦ")
        self.assertEqual(self.run_status(run_id), "completed")
        self.assertEqual(self.balance(), 0)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(UsageEvent)), 3)

    def test_rewrite_uses_upstream_text_and_style_instructions(self):
        self.fund(1)
        run_id = self.start(chain(("source", "source"),
                                  ("rewrite", "rewrite", {"instructions": "câu ngắn, giọng hào hứng", "tone": "friendly"})),
                            registry=with_source())
        prompt = self.job_payload(run_id, "rewrite")["prompt"]
        for expected in ("Rewrite the text below in Vietnamese", "Style: câu ngắn, giọng hào hứng.", "Tone: friendly.",
                         "Nội dung gốc về rừng đêm."):
            self.assertIn(expected, prompt)
        self.assertNotIn("Additional instructions", prompt)

    def test_summarize_and_translate_read_upstream_text(self):
        self.fund(2)
        graph = {"nodes": [{"id": "source", "type": "source", "x": 0, "y": 0},
                           {"id": "summary", "type": "summarize", "x": 1, "y": 0},
                           {"id": "english", "type": "translate", "x": 1, "y": 1, "config": {"target_language": "en"}}],
                 "edges": [{"source": "source", "target": "summary"}, {"source": "source", "target": "english"}]}
        run_id = self.start(graph, registry=with_source())
        self.assertIn('"""\nNội dung gốc về rừng đêm.\n"""', self.job_payload(run_id, "summary")["prompt"])
        english = self.job_payload(run_id, "english")
        self.assertIn("into English", english["prompt"])
        self.assertIn("Nội dung gốc về rừng đêm.", english["prompt"])
        self.assertEqual(english["max_tokens"], 4096)

    def test_nodes_without_input_or_tool_block_without_charging(self):
        self.fund(5)
        run_id = self.start(chain(("summary", "summarize")), project_id="empty")
        step = self.steps(run_id)["summary"]
        self.assertEqual((step.status, step.detail), ("blocked", MISSING_INPUT_DETAIL))
        self.assertEqual(json.loads(step.output)["error"]["code"], "missing_input")
        with self.Session.begin() as db:
            db.get(AITool, "tool-1").is_enabled = False
        run_id = self.start(chain(("writer", "ai_writer")))
        self.assertEqual(self.steps(run_id)["writer"].detail, MISSING_TOOL_DETAIL)
        self.assertEqual(self.balance(), 5)

    def test_list_nodes_return_their_options(self):
        self.fund(3)
        run_id = self.start(chain(("idea", "idea"), ("hook", "hook", {"count": 3}), ("title", "title"), ("cta", "cta")))
        self.assertIn("Write 3 alternative opening hooks", self.job_payload(run_id, "hook")["prompt"])
        self.work(FakeProvider('1. Hook một\n- Hook hai\n"Hook ba"\n'))
        self.assertEqual(self.output(run_id, "hook")["options"], ["Hook một", "Hook hai", "Hook ba"])
        self.assertIn("Write 5 alternative video titles", self.job_payload(run_id, "title")["prompt"])
        self.assertIn("Hook một", self.job_payload(run_id, "title")["prompt"])
        self.work(FakeProvider("Tiêu đề A\nTiêu đề B"))
        self.work(FakeProvider("Đăng ký kênh"))
        self.assertEqual(self.output(run_id, "cta")["options"], ["Đăng ký kênh"])
        self.assertEqual(self.run_status(run_id), "completed")

    # Failures and credits ---------------------------------------------------

    def test_provider_rejection_fails_the_step_and_refunds(self):
        self.fund(1)
        run_id = self.start(chain(("idea", "idea"), ("writer", "ai_writer"), ("summary", "summarize")))
        self.assertEqual(self.balance(), 0)
        self.work(FakeProvider(TextProviderError("invalid_request", "bad model", http_status=400)))
        steps = self.steps(run_id)
        self.assertEqual((steps["writer"].status, steps["writer"].detail), ("failed", text_worker.REJECTED_DETAIL))
        self.assertEqual(json.loads(steps["writer"].output)["error"], {"code": "invalid_request", "retryable": False})
        self.assertEqual(steps["summary"].status, "skipped")
        self.assertEqual(json.loads(steps["idea"].output)["topic"], "Rừng đêm")
        self.assertEqual(self.run_status(run_id), "failed")
        self.assertEqual(self.balance(), 1)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(UsageEvent)), 0)
            self.assertEqual(db.scalar(select(WorkflowJob.state)), "failed")
            self.assertEqual(sorted(db.scalars(select(CreditLedger.reason))), ["text_refund", "text_reserve"])

    def test_transient_errors_retry_then_succeed_or_refund(self):
        self.fund(2)
        run_id = self.start(chain(("writer", "ai_writer")))
        provider = FakeProvider(TextProviderError("rate_limited", "slow down", retryable=True), "Xong")
        self.work(provider)
        step = self.steps(run_id)["writer"]
        self.assertEqual((step.status, step.detail), ("queued", text_worker.RETRY_DETAIL))
        self.work(provider)
        self.assertEqual(self.output(run_id, "writer")["text"], "Xong")
        self.assertEqual(self.balance(), 1)

        run_id = self.start(chain(("writer", "ai_writer")))
        outage = TextProviderError("provider_unavailable", "down", retryable=True)
        provider = FakeProvider(outage, outage, outage)
        for _ in range(3):
            self.work(provider)
        step = self.steps(run_id)["writer"]
        self.assertEqual((step.status, step.detail), ("failed", text_worker.FAILED_DETAIL))
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(self.balance(), 1)

    def test_a_worker_that_died_mid_call_is_retried(self):
        self.fund(1)
        run_id = self.start(chain(("writer", "ai_writer")))
        with self.Session.begin() as db:
            job = jobs.claim_due_jobs(db, worker_id="crashed", lease_seconds=60)[0]
            db.get(WorkflowRunStep, job.step_id).status = "running"
            job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.work(FakeProvider("Viết lại từ đầu"))
        self.assertEqual(self.output(run_id, "writer")["text"], "Viết lại từ đầu")
        self.assertEqual(self.balance(), 0)

    def test_missing_api_key_blocks_or_refunds(self):
        self.fund(1)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            run_id = self.start(chain(("writer", "ai_writer")))
            step = self.steps(run_id)["writer"]
            self.assertEqual((step.status, step.detail), ("blocked", "Server cần OPENAI_API_KEY."))
            self.assertEqual(self.balance(), 1)
        run_id = self.start(chain(("writer", "ai_writer")))
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.work(text_worker.create_text_provider)
        step = self.steps(run_id)["writer"]
        self.assertEqual((step.status, json.loads(step.output)["error"]["code"]), ("failed", "missing_key"))
        self.assertEqual(self.balance(), 1)

    def test_insufficient_credits_rejects_the_run_or_blocks_a_later_step(self):
        with self.assertRaises(RunRequestError) as caught:
            self.start(chain(("writer", "ai_writer")))
        self.assertEqual((caught.exception.status_code, caught.exception.detail),
                         (402, "Not enough credits for this step"))
        with self.Session() as db:
            self.assertEqual(db.scalars(select(WorkflowRun)).all(), [])
        self.fund(1)
        run_id = self.start(chain(("writer", "ai_writer"), ("summary", "summarize")))
        self.work(FakeProvider("Bài viết"))
        step = self.steps(run_id)["summary"]
        self.assertEqual((step.status, step.detail), ("blocked", "Không đủ credits cho bước này."))
        self.assertEqual(json.loads(step.output)["error"]["code"], "insufficient_credits")
        self.assertEqual(self.steps(run_id)["writer"].status, "completed")
        self.assertEqual(self.run_status(run_id), "blocked")
        self.assertEqual(self.balance(), 0)

    def test_readiness_per_text_node(self):
        graph = chain(("writer", "ai_writer"), ("summary", "summarize"))
        with self.Session() as db:
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), graph=graph)
            checks = [check for _, check in WorkflowExecutor().readiness(context)]
        self.assertEqual([(c.status, c.credits) for c in checks], [("insufficient_credits", 1)] * 2)
        self.fund(2)
        with self.Session() as db, patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), graph=graph)
            self.assertEqual(WorkflowExecutor().readiness(context)[0][1].status, "missing_key")
        with self.Session() as db:
            context = ExecutionContext(db, workspace=db.get(Workspace, "space-1"), graph=graph)
            self.assertEqual({c.status for _, c in WorkflowExecutor().readiness(context)}, {"ready"})

    def test_settings_are_validated_per_node_type(self):
        writer = default_registry.resolve("ai_writer")
        writer.validate_config({"prompt": "x", "language": "vi", "tone": "calm", "platform": "tiktok",
                                "duration": 30, "temperature": 0.7, "max_tokens": 512, "tool_id": "tool-1"})
        default_registry.resolve("translate").validate_config({"target_language": "pt-BR"})
        default_registry.resolve("hook").validate_config({"count": 10})
        bad = [("ai_writer", {"voice": "x"}), ("ai_writer", {"duration": 2}), ("ai_writer", {"language": "Vietnamese"}),
               ("ai_writer", {"prompt": "x" * 3001}), ("summarize", {"prompt": "x"}), ("hook", {"count": 11}),
               ("rewrite", {"temperature": True}), ("idea", {"prompt": "x"})]
        for node_type, config in bad:
            with self.subTest(node_type=node_type, config=config), self.assertRaises(ValueError):
                default_registry.resolve(node_type).validate_config(config)


class TextApiTest(unittest.TestCase):
    """Text nodes through the HTTP API: settings round-trip, readiness, run and worker."""

    def test_text_workflow_over_http(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(
                json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            program = r'''
import os
os.environ["OPENAI_API_KEY"] = "sk-test"
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
command.upgrade(Config("alembic.ini"), "head")
from app.main import app
from app.db import Session
from app import text_worker, usage
from app.providers.text import TextResult, TextUsage
client = TestClient(app)
assert client.post("/api/setup", json={"email":"a@example.com", "password":"long-password-123"}).status_code == 200
workspace = client.get("/api/dashboard").json()["workspace"]["id"]
project = client.post("/api/projects", json={"title":"A", "topic":"Rừng đêm"}).json()["id"]
workflow = client.post("/api/workflows", json={"name":"Text"}).json()["id"]
graph = {"nodes": [{"id":"idea","type":"idea","x":0,"y":0},
                   {"id":"writer","type":"ai_writer","x":1,"y":0,"config":{"tone":"cinematic","duration":45}},
                   {"id":"title","type":"title","x":2,"y":0}],
         "edges": [{"source":"idea","target":"writer"}, {"source":"writer","target":"title"}]}
saved = client.put(f"/api/workflows/{workflow}", json=graph)
assert saved.status_code == 200, saved.text
assert saved.json()["graph"]["nodes"][1]["config"] == {"tone":"cinematic","duration":45}
listed = next(w for w in client.get("/api/dashboard").json()["workflows"] if w["id"] == workflow)
assert listed["graph"]["nodes"][1]["config"] == {"tone":"cinematic","duration":45}
bad = client.put(f"/api/workflows/{workflow}", json={**graph, "nodes":[{**graph["nodes"][1], "config":{"duration":1}}], "edges":[]})
assert bad.status_code == 422 and "duration" in bad.json()["detail"], bad.text
assert client.put(f"/api/workflows/{workflow}", json={"nodes":[{**graph["nodes"][0], "config":{"x":1}}], "edges":[]}).status_code == 422
readiness = client.get(f"/api/workflows/{workflow}/readiness").json()
assert [s["status"] for s in readiness["steps"]] == ["configured", "missing_tool", "missing_tool"], readiness
assert client.post("/api/ai-tools", json={"task":"script","provider":"openai","model":"gpt-4.1-mini"}).status_code == 201
readiness = client.get(f"/api/workflows/{workflow}/readiness").json()
assert readiness["credits_required"] == 2 and not readiness["runnable"], readiness
assert client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project}).status_code == 402
with Session.begin() as db: usage.post_credit(db, workspace, 2, "test", "fund-text")
assert client.get(f"/api/workflows/{workflow}/readiness").json()["runnable"]
run = client.post(f"/api/workflows/{workflow}/runs", json={"project_id": project})
assert run.status_code == 201, run.text
run = run.json()
assert run["status"] == "running" and [s["status"] for s in run["steps"]] == ["completed", "queued", "skipped"], run
class Fake:
    def __init__(self, provider): self.provider = provider
    def generate(self, **kwargs):
        text = "Bài viết" if "Brief" in kwargs["prompt"] else "Tiêu đề 1\nTiêu đề 2"
        return TextResult(text=text, usage=TextUsage.of(10, 20), provider=self.provider, model=kwargs["model"])
    def close(self): pass
assert text_worker.run_one(provider_factory=Fake)
middle = client.get(f"/api/workflow-runs/{run['id']}").json()
assert [s["status"] for s in middle["steps"]] == ["completed", "completed", "queued"], middle
assert middle["steps"][1]["output"]["text"] == "Bài viết"
assert text_worker.run_one(provider_factory=Fake)
done = client.get(f"/api/workflow-runs/{run['id']}").json()
assert done["status"] == "completed", done
assert done["steps"][2]["output"]["options"] == ["Tiêu đề 1", "Tiêu đề 2"]
usage_view = client.get("/api/usage").json()
assert usage_view["balance"] == 0 and [e["tool"] for e in usage_view["events"]] == ["openai/text", "openai/text"]
'''
            completed = subprocess.run([sys.executable, "-c", program], cwd=target,
                                       env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
