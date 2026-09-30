"""Paid-worker safety using mocked providers and disposable SQLite workspaces."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SETUP = r'''
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import select, func
os.environ.update(FAL_KEY="test-key", OPENAI_API_KEY="test-key", VIDEO_CREDITS_PER_CLIP="10", TEXT_CREDITS_PER_GENERATION="1")
from app.db import Session, engine
from app.models import Base, User, Workspace, Project, Workflow, AITool, WorkflowRun, WorkflowRunStep, WorkflowJob, CreditAccount, CreditLedger, UsageEvent, Asset
Base.metadata.create_all(engine)
from app import usage, jobs, video_worker, text_worker
from app.workflow import ExecutionContext, default_executor
from app.providers.fal import Submission, JobStatus, JobFailure, ProviderError, VideoResult
from app.providers.text import TextProviderError, TextResult, TextUsage
with Session.begin() as db:
    db.add(User(id="user", email="worker@example.com", password_hash="test"))
    db.flush()
    db.add(Workspace(id="workspace", name="Test", owner_id="user"))
    db.flush()
    db.add(Project(id="project", workspace_id="workspace", title="Test", topic="A forest"))
    db.add(Workflow(id="workflow", workspace_id="workspace", name="Test"))
    db.add(CreditAccount(workspace_id="workspace", balance=100))
    db.add(AITool(id="video-tool", workspace_id="workspace", task="video", provider="fal", model="fal-ai/veo3.1/fast"))
    db.add(AITool(id="text-tool", workspace_id="workspace", task="script", provider="openai", model="gpt-4.1-mini"))

def new_run(kind="video", sibling=False):
    node_type = "video" if kind == "video" else "ai_writer"
    nodes = [{"id": "paid", "type": node_type, "x": 0, "y": 0}]
    if sibling:
        nodes.append({"id": "sibling", "type": "ai_writer", "x": 0, "y": 1})
    graph = {"nodes": nodes, "edges": []}
    with Session.begin() as db:
        run = WorkflowRun(id=str(uuid4()), workspace_id="workspace", workflow_id="workflow", project_id="project", graph_snapshot=json.dumps(graph))
        db.add(run)
        db.flush()
        default_executor.start_run(ExecutionContext.for_run(db, run))
        step = db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id, WorkflowRunStep.node_id == "paid"))
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.step_id == step.id))
        assert step.status == "queued", (step.status, step.detail)
        return run.id, step.id, job.id

def snapshot(run_id, step_id):
    with Session() as db:
        step = db.get(WorkflowRunStep, step_id)
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.step_id == step_id))
        return dict(status=step.status, detail=step.detail, output=json.loads(step.output or "{}"), run_status=db.get(WorkflowRun, run_id).status, balance=db.get(CreditAccount, "workspace").balance, job_state=job.state, token=job.lease_token)

class Accepted:
    submitted = 0
    def submit(self, request):
        self.submitted += 1
        return Submission(model_id=request.model_id, request_id="request-1", status_url="https://queue.fal.run/fal-ai/veo3.1/fast/requests/request-1/status", response_url="https://queue.fal.run/fal-ai/veo3.1/fast/requests/request-1")
    def status(self, submission):
        return JobStatus("running")

class LostText:
    called = 0
    def generate(self, **kwargs):
        self.called += 1
        raise TextProviderError("timeout", "SECRET signed-url?token=secret", retryable=True)
    def close(self): pass
'''


class ReconciliationWorkerTest(unittest.TestCase):
    def run_isolated(self, case):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copytree(ROOT / "app", target / "app", ignore=shutil.ignore_patterns("__pycache__"))
            (target / "instance").mkdir()
            (target / "instance" / "bootstrap.json").write_text(
                json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            result = subprocess.run([sys.executable, "-c", SETUP + "\n" + case], cwd=target,
                                    env={**os.environ, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[-6000:])

    def test_full_storage_refunds_without_submitting(self):
        self.run_isolated(r'''
run_id, step_id, job_id = new_run()
with Session.begin() as db:
    db.add(Asset(id="existing", workspace_id="workspace", filename="existing.mp4", content_type="video/mp4", bytes=100))
fake = Accepted()
with patch.object(video_worker, "workspace_media_quota", return_value=100):
    assert video_worker.run_one(client=fake, poll_seconds=0)
state = snapshot(run_id, step_id)
assert state["status"] == "failed", state
assert fake.submitted == 0, "Full storage must be rejected before paid provider submission"
assert state["balance"] == 100, state
assert state["output"]["error"]["code"] == "storage_limit_exceeded", state
assert state["output"]["provider_job"]["submission_succeeded"] is False
assert not video_worker.run_one(client=fake)
with Session() as db:
    assert db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reason == "video_refund")) == 1
    assert db.scalar(select(func.count()).select_from(UsageEvent)) == 0
''')

    def test_video_progress_and_untrusted_failure_are_sanitized(self):
        self.run_isolated(r'''
run_id, step_id, job_id = new_run()
fake = Accepted()
video_worker.run_one(client=fake, poll_seconds=0)
state = snapshot(run_id, step_id)
progress = state["output"].get("provider_job", {})
assert progress.get("submission_succeeded") is True, state
assert progress["submission_started_at"] and progress["submitted_at"]
assert progress["remote_request_id"] == "request-1"
assert progress["stage"] == "submitted"
fake.status = lambda submission: JobStatus("failed", error=JobFailure("SECRET https://signed?token=secret", "SECRET body", False))
video_worker.run_one(client=fake, poll_seconds=0)
state = snapshot(run_id, step_id)
assert state["status"] == "needs_attention" and state["balance"] == 90, state
assert state["output"]["provider_job"]["last_polled_at"]
assert state["output"]["provider_job"]["last_provider_status"] == "failed"
assert state["output"]["error"] == {"code": "provider_failed", "category": "generation_failed", "retryable": False}, state
assert "SECRET" not in json.dumps(state) and "signed?" not in json.dumps(state), state
assert not video_worker.run_one(client=fake)
''')

    def test_ambiguous_video_keeps_sibling_run_active(self):
        self.run_isolated(r'''
run_id, step_id, job_id = new_run(sibling=True)
class LostVideo:
    def submit(self, request): raise TimeoutError("SECRET")
video_worker.run_one(client=LostVideo(), poll_seconds=0)
state = snapshot(run_id, step_id)
assert state["status"] == "needs_attention", state
assert state["run_status"] == "running", "A queued sibling must keep the aggregate run active"
assert state["output"]["error"]["code"] == "submission_unknown", state
assert state["output"]["provider_job"]["submission_started_at"]
assert state["output"]["provider_job"]["submitted_at"] is None
''')

    def test_text_timeout_is_held_without_retry_or_refund(self):
        self.run_isolated(r'''
run_id, step_id, job_id = new_run("text")
fake = LostText()
assert text_worker.run_one(provider_factory=lambda name: fake, session_factory=Session, retry_seconds=0)
state = snapshot(run_id, step_id)
assert state["status"] == "needs_attention", state
assert state["balance"] == 99 and state["job_state"] == "failed", state
assert state["output"]["error"] == {"code": "timeout", "category": "timeout", "retryable": False}, state
assert state["output"]["provider_job"]["submission_started_at"]
assert "SECRET" not in json.dumps(state)
assert not text_worker.run_one(provider_factory=lambda name: fake, session_factory=Session, retry_seconds=0)
assert fake.called == 1
''')

    def test_reclaimed_text_request_is_not_submitted_again(self):
        self.run_isolated(r'''
run_id, step_id, job_id = new_run("text")
with Session.begin() as db:
    job = jobs.claim_due_jobs(db, worker_id="crashed", lease_seconds=60)[0]
    db.get(WorkflowRunStep, step_id).status = "running"
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
fake = LostText()
text_worker.run_one(provider_factory=lambda name: fake, session_factory=Session, retry_seconds=0)
state = snapshot(run_id, step_id)
assert state["status"] == "needs_attention", state
assert fake.called == 0, "The original call might already have been billed"
assert state["balance"] == 99
assert state["output"]["error"]["code"] == "submission_unknown"
''')

    def test_terminal_video_step_cannot_submit_or_be_overwritten(self):
        self.run_isolated(r'''
run_id, step_id, job_id = new_run()
with Session.begin() as db:
    step = db.get(WorkflowRunStep, step_id)
    step.status = "failed"
    step.detail = "Reconciled decision"
    step.output = json.dumps({"reconciliation": {"status": "resolved_no_charge"}})
fake = Accepted()
video_worker.run_one(client=fake, poll_seconds=0)
state = snapshot(run_id, step_id)
assert state["status"] == "failed" and state["detail"] == "Reconciled decision", state
assert fake.submitted == 0
assert state["job_state"] == "failed"
''')

    def test_persisted_reconciliation_blocks_accidentally_requeued_jobs(self):
        self.run_isolated(r'''
from app.models import CreditReconciliation
for kind in ("video", "text"):
    run_id, step_id, job_id = new_run(kind)
    with Session.begin() as db:
        reference = f"video-reserve:{step_id}:single" if kind == "video" else f"text-reserve:{step_id}"
        reservation = db.scalar(select(CreditLedger).where(CreditLedger.reference == reference))
        db.add(CreditReconciliation(step_id=step_id, job_id=job_id, reservation_id=reservation.id,
            decision="confirmed_charge", credits=10 if kind == "video" else 1,
            reconciled_by="user", reconciled_at=datetime.now(timezone.utc)))
    video, text = Accepted(), LostText()
    if kind == "video": video_worker.run_one(client=video, poll_seconds=0)
    else: text_worker.run_one(provider_factory=lambda name: text, session_factory=Session, retry_seconds=0)
    state = snapshot(run_id, step_id)
    assert video.submitted == 0 and text.called == 0, "Reconciled jobs cannot make another paid request"
    assert state["job_state"] == "failed", state
''')

    def test_text_server_errors_and_invalid_responses_require_reconciliation(self):
        self.run_isolated(r'''
for code in ("provider_unavailable", "network_error", "invalid_response", "empty_output", "content_rejected"):
    run_id, step_id, job_id = new_run("text")
    class FailedText(LostText):
        def generate(self, **kwargs):
            self.called += 1
            raise TextProviderError(code, "SECRET", retryable=True)
    fake = FailedText()
    text_worker.run_one(provider_factory=lambda name: fake, session_factory=Session, retry_seconds=0)
    state = snapshot(run_id, step_id)
    assert state["status"] == "needs_attention" and state["job_state"] == "failed", state
    assert state["output"]["error"]["code"] == code, state
    assert not text_worker.run_one(provider_factory=lambda name: fake, session_factory=Session, retry_seconds=0)
with Session() as db:
    assert db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reason == "text_refund")) == 0
''')

    def test_text_success_preserves_request_timing_and_response_id(self):
        self.run_isolated(r'''
run_id, step_id, job_id = new_run("text")
class GoodText(LostText):
    def generate(self, **kwargs):
        return TextResult("Done", TextUsage.of(2, 3), "openai", kwargs["model"], {"response_id": "chatcmpl-123", "finish_reason": "stop"})
text_worker.run_one(provider_factory=lambda name: GoodText(), session_factory=Session)
state = snapshot(run_id, step_id)
progress = state["output"]["provider_job"]
assert progress["submission_started_at"] and progress["submitted_at"], state
assert progress["remote_request_id"] == "chatcmpl-123", state
assert state["status"] == "completed" and state["balance"] == 99
''')

    def test_expired_leases_cannot_settle_or_refund(self):
        self.run_isolated(r'''
run_id, step_id, job_id = new_run()
class ExpiringVideo(Accepted):
    def submit(self, request):
        with Session.begin() as db:
            db.get(WorkflowJob, job_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        return super().submit(request)
fake = ExpiringVideo()
video_worker.run_one(client=fake, poll_seconds=0)
state = snapshot(run_id, step_id)
assert state["status"] == "submitting" and state["balance"] == 90, state
video_worker.run_one(client=Accepted(), poll_seconds=0)
state = snapshot(run_id, step_id)
assert state["status"] == "needs_attention" and state["balance"] == 90, state
video_worker._terminal_failure(job_id, "stale-token", "stale", refund=True)
assert snapshot(run_id, step_id) == state
''')

    def test_worker_refunds_lock_run_before_credit_account(self):
        self.run_isolated(r'''
from sqlalchemy import event
for kind in ("video", "text"):
    run_id, step_id, job_id = new_run(kind)
    locks = []
    def record_locks(execution):
        statement = execution.statement
        if statement.is_select and statement._for_update_arg is not None:
            locks.extend(table.name for table in statement.get_final_froms())
    event.listen(Session, "do_orm_execute", record_locks)
    try:
        class RejectedVideo:
            def submit(self, request): raise ProviderError("invalid_request", "Rejected", http_status=400)
        class RejectedText(LostText):
            def generate(self, **kwargs): raise TextProviderError("invalid_request", "Rejected", http_status=400)
        if kind == "video": video_worker.run_one(client=RejectedVideo())
        else: text_worker.run_one(provider_factory=lambda name: RejectedText(), session_factory=Session)
    finally:
        event.remove(Session, "do_orm_execute", record_locks)
    assert "workflow_runs" in locks and "credit_accounts" in locks, locks
    assert locks.index("workflow_runs") < locks.index("credit_accounts"), (kind, locks)
    assert snapshot(run_id, step_id)["status"] == "failed"
''')
