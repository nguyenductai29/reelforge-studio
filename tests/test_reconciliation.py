"""Admin accounting integration, always against a disposable migrated database."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReconciliationAPITest(unittest.TestCase):
    def test_accounting_authorization_history_and_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for folder in ("app", "migrations"):
                shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
            (target / "instance").mkdir()
            (target / "instance/bootstrap.json").write_text(json.dumps({"database_url": f"sqlite:///{target}/instance/test.db"}))
            program = r'''
import json, os, logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text, inspect
os.environ['FAL_KEY'] = 'fake-key-only'
os.environ['VIDEO_CREDITS_PER_CLIP'] = '10'
command.upgrade(Config('alembic.ini'), '0010_publications')
from app.main import app
from app.db import Session, engine
from app.models import CreditAccount, CreditLedger, UsageEvent, WorkflowJob, WorkflowRun, WorkflowRunStep, User, Asset
from app import usage, video_worker
admin = TestClient(app)
assert admin.post('/api/setup', json={'email':'admin@example.com','password':'long-password-123'}).status_code == 200
workspace = admin.get('/api/dashboard').json()['workspace']['id']
project = admin.post('/api/projects', json={'title':'Test','topic':'A forest'}).json()['id']
workflow = admin.post('/api/workflows', json={'name':'Video'}).json()['id']
assert admin.post('/api/ai-tools', json={'task':'video','provider':'fal','model':'fal-ai/veo3.1/fast'}).status_code == 201
with Session.begin() as db:
    usage.post_credit(db, workspace, 1000, 'test', 'test-funding')
    admin_id = db.scalar(select(User.id).where(User.email == 'admin@example.com'))
historical = admin.post(f'/api/workflows/{workflow}/runs', json={'project_id':project}).json()['id']
with Session.begin() as db:
    historical_job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == historical))
    historical_job.state = 'succeeded'
    historical_step = db.get(WorkflowRunStep, historical_job.step_id)
    historical_step.status = 'completed'
    db.get(WorkflowRun, historical).status = 'completed'
    db.add(Asset(id='migration-asset',workspace_id=workspace,project_id=project,run_id=historical,
                 step_id=historical_step.id,filename='old.mp4',content_type='video/mp4',bytes=12))
    db.add(UsageEvent(id='migration-usage',workspace_id=workspace,tool='fal/video',units=1,credits=10,
                      reference=f'video:{historical_step.id}',created_at=datetime.now(timezone.utc)))
    db.flush()
    # Raw SQL: the Publication model already has the columns migration 0013 adds later.
    db.execute(text("INSERT INTO publications (id, workspace_id, run_id, asset_id, channel, title, description, state, "
                    "remote_id, created_at, updated_at) VALUES ('migration-publication', :workspace, :run, "
                    "'migration-asset', 'youtube', 'Old', '', 'succeeded', 'remote1', :now, :now)"),
               {"workspace": workspace, "run": historical, "now": datetime.now(timezone.utc)})
old_tables = [table for table in inspect(engine).get_table_names() if table != 'alembic_version']
# Later migrations may add columns (0013 adds publication privacy and tags); the old columns must not change.
old_columns = {table: [column['name'] for column in inspect(engine).get_columns(table)] for table in old_tables}
def snapshot():
    with engine.connect() as connection:
        return {table: sorted([repr(tuple(row)) for row in connection.execute(
                    text('SELECT ' + ', '.join(old_columns[table]) + ' FROM ' + table))])
                for table in old_tables}
before_upgrade = snapshot()
command.upgrade(Config('alembic.ini'), 'head')
assert snapshot() == before_upgrade  # all old tables/records, not only balances
# Additive migration preserves old account/ledger/user/project/workflow records.
with Session() as db:
    assert db.get(CreditAccount, workspace).balance == 990
    assert db.scalar(select(CreditLedger.delta).where(CreditLedger.reference == 'test-funding')) == 1000
response = admin.get('/api/admin/reconciliation')
assert response.status_code == 200, response.text
assert response.json() == {'items': [], 'total': 0}, response.text
from app.models import CreditReconciliation
recorded_events = []
class AuditHandler(logging.Handler):
    def emit(self, record):
        if not getattr(record, 'event', '').startswith('reconciliation_'): return
        fields = record.fields
        assert {'admin_user_id','workspace_id','run_id','step_id','job_id','credits','provider'} <= fields.keys()
        with Session() as db:
            assert db.scalar(select(CreditReconciliation).where(CreditReconciliation.step_id == fields['step_id'])) is not None  # log after commit
        recorded_events.append((record.event, fields['step_id']))
audit_logger = logging.getLogger('app.reconciliation')
audit_logger.setLevel(logging.INFO)
audit_logger.addHandler(AuditHandler())
with Session() as db:
    original_ledger = {row.id: (row.delta,row.reference,row.reason) for row in db.scalars(select(CreditLedger))}

class Unknown:
    def submit(self, request): raise TimeoutError('lost response')
class Rejected:
    def submit(self, request):
        from app.providers.fal import ProviderError
        raise ProviderError('invalid_request', 'rejected', http_status=400)

def create_attention():
    result = admin.post(f'/api/workflows/{workflow}/runs', json={'project_id':project})
    assert result.status_code == 201, result.text
    run = result.json()['id']
    assert video_worker.run_one(client=Unknown(), poll_seconds=0)
    with Session() as db:
        job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run))
        return run, job.step_id, job.id

def resolve(step, action, note='checked provider dashboard'):
    return admin.post(f'/api/admin/reconciliation/{step}/{action}', json={'note':note})

def balance():
    with Session() as db:
        account = db.get(CreditAccount, workspace)
        ledger = db.scalar(select(func.sum(CreditLedger.delta)).where(CreditLedger.workspace_id == workspace))
        assert account.balance == ledger and account.balance >= 0, (account.balance, ledger)
        return account.balance

run, step, job = create_attention()
pending = admin.get('/api/admin/reconciliation').json()
assert pending['total'] == 1, pending
item = pending['items'][0]
assert item['step_id'] == step and item['job_id'] == job and item['credits'] == 10
assert item['workspace_id'] == workspace and item['user_email'] == 'admin@example.com'
assert item['provider'] == 'fal' and item['reconciliation_status'] == 'pending'
assert not item['has_asset'] and not item['submission_succeeded']
assert 'fake-key-only' not in json.dumps(pending) and 'prompt' not in item
assert admin.post(f'/api/workflow-runs/{run}/retry').status_code == 409
anonymous = TestClient(app)
assert anonymous.get('/api/admin/reconciliation').status_code == 401
assert anonymous.post(f'/api/admin/reconciliation/{step}/refund', json={}).status_code == 401
owner = TestClient(app)
registration = owner.post('/api/register', json={'email':'owner@example.com','password':'long-password-123','workspace_name':'Owner'})
assert registration.status_code == 201, registration.text
assert owner.get('/api/admin/reconciliation').status_code == 403
for action in ['refund', 'confirm-charge']:
    assert owner.post(f'/api/admin/reconciliation/{step}/{action}', json={}).status_code == 403
assert admin.post(f'/api/admin/reconciliation/{step}/refund', json={}, headers={'Origin':'https://evil.example'}).status_code == 403
assert resolve(step, 'refund', 'x'*1001).status_code == 422
before = balance()
first = resolve(step, 'refund')
assert first.status_code == 200, first.text
assert first.json()['reconciliation_status'] == 'refunded'
assert first.json()['reconciled_by'] == admin_id and first.json()['note'] == 'checked provider dashboard'
assert balance() == before + 10
second = resolve(step, 'refund', 'must not replace first note')
assert second.status_code == 200 and second.json() == first.json(), second.text
assert balance() == before + 10
assert resolve(step, 'confirm-charge').status_code == 409
assert admin.get('/api/admin/reconciliation').json()['total'] == 0
assert admin.get('/api/admin/reconciliation?status=resolved').json()['items'][0]['step_id'] == step
with Session() as db:
    decision = db.scalar(select(CreditReconciliation).where(CreditReconciliation.step_id == step))
    assert decision.decision == 'refunded' and decision.credits == 10
    assert db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reference == f'video-refund:{step}:single')) == 1
assert admin.get(f'/api/workflow-runs/{run}').json()['status'] == 'failed'
retried = admin.post(f'/api/workflow-runs/{run}/retry')
assert retried.status_code == 201, retried.text
new_run = retried.json()['id']
assert new_run != run and balance() == before
with Session() as db:
    new_job = db.scalar(select(WorkflowJob).where(WorkflowJob.run_id == new_run))
    assert new_job.id != job and new_job.step_id != step
    assert db.scalar(select(CreditLedger.delta).where(CreditLedger.reference == f'video-reserve:{new_job.step_id}:single')) == -10
video_worker.run_one(client=Rejected(), poll_seconds=0)
assert admin.get('/api/admin/reconciliation').json()['total'] == 0  # deterministic failure excluded

charged_run, charged_step, _ = create_attention()
before = balance()
charged = resolve(charged_step, 'confirm-charge')
assert charged.status_code == 200, charged.text
assert charged.json()['reconciliation_status'] == 'confirmed_charge' and balance() == before
assert resolve(charged_step, 'confirm-charge').status_code == 200
assert balance() == before
assert resolve(charged_step, 'refund').status_code == 409
with Session() as db:
    events = list(db.scalars(select(UsageEvent).where(UsageEvent.reference == f'video:{charged_step}:single')))
    assert len(events) == 1 and events[0].credits == 10
assert admin.get(f'/api/workflow-runs/{charged_run}').json()['status'] == 'needs_attention'
assert admin.post(f'/api/workflow-runs/{charged_run}/retry').status_code == 409

# Opposite concurrent decisions serialize; exactly one wins.
race_run, race_step, _ = create_attention()
before = balance()
with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(lambda action: resolve(race_step, action), ['refund','confirm-charge']))
assert sorted(r.status_code for r in results) == [200,409], [(r.status_code,r.text) for r in results]
with Session() as db:
    decision = db.scalar(select(CreditReconciliation).where(CreditReconciliation.step_id == race_step))
    assert balance() == before + (10 if decision.decision == 'refunded' else 0)

# Same-action concurrency still creates only one refund.
same_run, same_step, _ = create_attention()
before = balance()
with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(lambda _: resolve(same_step, 'refund'), range(2)))
assert [r.status_code for r in results] == [200,200], [r.text for r in results]
assert balance() == before + 10

# Legacy output lacking new metadata remains visible; remote URLs never leak.
legacy_run, legacy_step, legacy_job = create_attention()
with Session.begin() as db:
    s = db.get(WorkflowRunStep, legacy_step)
    s.output = json.dumps({'submission': {'request_id':'legacy-job-1','status_url':'https://secret.example/?token=hidden'},
                          'provider_job':['legacy'], 'error':'legacy error'})
item = next(x for x in admin.get('/api/admin/reconciliation').json()['items'] if x['step_id'] == legacy_step)
assert item['remote_request_id'] == 'legacy-job-1' and item['submission_succeeded']
assert 'secret.example' not in json.dumps(item) and 'hidden' not in json.dumps(item)
assert resolve(legacy_step, 'refund').status_code == 200

# A prior matching refund/usage can be acknowledged but never duplicated/reversed.
prior_run, prior_step, _ = create_attention()
with Session.begin() as db:
    usage.post_credit(db, workspace, 10, 'video_refund', f'video-refund:{prior_step}:single')
before = balance()
assert resolve(prior_step, 'confirm-charge').status_code == 409
assert resolve(prior_step, 'refund').status_code == 200 and balance() == before
prior_run, prior_step, _ = create_attention()
with Session.begin() as db:
    db.add(UsageEvent(id='prior-usage',workspace_id=workspace,tool='fal/video',units=1,credits=10,
                      reference=f'video:{prior_step}:single',created_at=datetime.now(timezone.utc)))
before = balance()
assert resolve(prior_step, 'refund').status_code == 409
assert resolve(prior_step, 'confirm-charge').status_code == 200 and balance() == before
with Session() as db:
    assert db.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.reference == f'video:{prior_step}:single')) == 1

# A generic text reservation can also be resolved; no video handler involved.
text_run, text_step, text_job = create_attention()
with Session.begin() as db:
    j = db.get(WorkflowJob,text_job); p = j.payload
    p.update(kind='text.generate',node_type='ai_writer',provider='gemini',model='test-text',credits=1)
    for key in ('reserve_reference','usage_reference','refund_reference','mode','operation'): p.pop(key)
    j.payload_json = json.dumps(p); j.logical_key = f'text:{text_run}:{text_step}'
    db.get(WorkflowRunStep,text_step).node_type = 'ai_writer'
    usage.post_credit(db,workspace,10,'video_refund',f'refund:{text_run}')
    usage.post_credit(db,workspace,-1,'text_reserve',f'text-reserve:{text_step}')
before = balance()
assert resolve(text_step,'refund').status_code == 200 and balance() == before+1

# Reservation amount remains frozen even if the configured quote changes.
fixed_run, fixed_step, _ = create_attention()
os.environ['VIDEO_CREDITS_PER_CLIP'] = '50'
before = balance()
assert resolve(fixed_step,'refund').json()['credits'] == 10 and balance() == before+10
os.environ['VIDEO_CREDITS_PER_CLIP'] = '10'

# A refund may be granted with an asset, but retry must not generate it again.
asset_run, asset_step, _ = create_attention()
with Session.begin() as db:
    db.add(Asset(id='existing-asset',workspace_id=workspace,project_id=project,run_id=asset_run,step_id=asset_step,
                 filename='old.mp4',content_type='video/mp4',bytes=12))
assert resolve(asset_step, 'refund').status_code == 200
assert admin.post(f'/api/workflow-runs/{asset_run}/retry').status_code == 409

# Amount comes from immutable reservation; mismatched job data is rejected atomically.
bad_run, bad_step, bad_job = create_attention()
with Session.begin() as db:
    j = db.get(WorkflowJob, bad_job)
    p = j.payload; p['credits'] = 999; j.payload_json = json.dumps(p)
before = balance()
assert resolve(bad_step, 'refund').status_code == 409
assert balance() == before
with Session.begin() as db:
    j = db.get(WorkflowJob, bad_job)
    p = j.payload; p['credits'] = 10; j.payload_json = json.dumps(p)
    j.workspace_id = owner.get('/api/dashboard').json()['workspace']['id']
assert resolve(bad_step, 'refund').status_code == 409
assert balance() == before
assert resolve('missing-step', 'refund').status_code == 404
assert admin.get('/api/admin/reconciliation?status=all').status_code == 422
assert admin.get('/api/admin/reconciliation?limit=1000').status_code == 422
assert recorded_events.count(('reconciliation_refunded',step)) == 1
assert recorded_events.count(('reconciliation_charge_confirmed',charged_step)) == 1

# Confirm charge can finalize a fully spent account without another debit.
zero_run, zero_step, _ = create_attention()
with Session.begin() as db:
    remaining = db.get(CreditAccount,workspace).balance
    usage.post_credit(db,workspace,-remaining,'test','spend-remaining')
assert balance() == 0
assert resolve(zero_step,'confirm-charge').status_code == 200 and balance() == 0
# Upper bound rejection rolls back decision, step change and ledger together.
with Session.begin() as db: usage.post_credit(db,workspace,10,'test','fund-cap-case')
cap_run, cap_step, _ = create_attention()
with Session.begin() as db: usage.post_credit(db,workspace,2_000_000_000,'test','reach-cap')
assert resolve(cap_step,'refund').status_code == 409
assert balance() == 2_000_000_000
with Session() as db:
    assert db.scalar(select(CreditReconciliation).where(CreditReconciliation.step_id == cap_step)) is None
    assert db.get(WorkflowRunStep,cap_step).status == 'needs_attention'
    for ledger_id, original in original_ledger.items():
        row = db.get(CreditLedger,ledger_id)
        assert (row.delta,row.reference,row.reason) == original
print('Reconciliation accounting/API scenarios passed')
'''
            result = subprocess.run([sys.executable, "-c", program], cwd=target,
                                    env={**os.environ, "PYTHONPATH": str(target)}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
