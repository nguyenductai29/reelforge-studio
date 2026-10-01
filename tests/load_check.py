"""Basic load verification: sign-in, dashboard, notifications, job claiming and payment callbacks.

    python tests/load_check.py --database-url postgresql://postgres:postgres@127.0.0.1:5432/reelforge_load_test

It needs an isolated PostgreSQL database whose name contains "test": the database is dropped and created again.
A copy of the API (app/, migrations/) runs on 127.0.0.1 as production runs it (one uvicorn process), with its own
master key, and is seeded through its own HTTP API. Each scenario then runs at a fixed concurrency; the script
prints latency percentiles, throughput and errors as a Markdown table (docs/LOAD_BASELINE.md records a run).

Nothing here tunes the application; it measures it. No external service is contacted: email is off, and the payOS
callbacks are signed locally with a throwaway checksum key, exactly as payOS signs them.
Each simulated client sends its own CF-Connecting-IP (TEST-NET addresses), which the API believes only because the
requests come from loopback, as they do behind Cloudflare's tunnel; the per-address limits stay in force.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from cryptography.fernet import Fernet
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:3999"
PASSWORD = "load-check-password-1"
_addresses = (f"203.0.113.{a}" if b == 0 else f"198.51.{b}.{a}" for b in range(0, 101) for a in range(1, 255))
_address_lock = threading.Lock()


def address() -> str:
    with _address_lock:
        return next(_addresses)


def headers(**extra) -> dict:
    return {"Origin": ORIGIN, "CF-Connecting-IP": address(), **extra}


# --- the disposable installation ----------------------------------------------------------------------------------

def checked_url(raw: str):
    url = make_url(raw)
    if not url.drivername.startswith("postgresql") or "test" not in (url.database or "").lower():
        raise SystemExit("--database-url must name a PostgreSQL database whose name contains 'test'")
    return url.set(drivername="postgresql+psycopg")


def install(url, work: Path) -> dict:
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, work / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", work / "alembic.ini")
    (work / "instance").mkdir()
    (work / "instance" / "bootstrap.json").write_text(json.dumps({
        "database_url": url.render_as_string(hide_password=False), "frontend_origin": ORIGIN, "secure_cookies": False}))
    key = work / "instance" / "master.key"
    key.write_text(Fernet.generate_key().decode() + "\n")
    server = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    server.dispose()
    env = {**os.environ, "PYTHONPATH": str(work), "REELFORGE_MASTER_KEY_FILE": str(key), "PYTHONIOENCODING": "utf-8"}
    env.pop("REELFORGE_DATABASE_URL", None)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=work, env=env, check=True,
                   capture_output=True)
    return env


def start_api(work: Path, env: dict, port: int):
    log = open(work / "api.log", "w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                "--port", str(port), "--log-level", "warning"],
                               cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health/ready", timeout=2).status_code == 200:
                return process, log
        except httpx.HTTPError:
            pass
        if process.poll() is not None:
            break
        time.sleep(0.3)
    process.terminate()
    raise SystemExit(f"the API did not become ready; see {work / 'api.log'}")


# --- measuring ----------------------------------------------------------------------------------------------------

def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))]


def measure(name: str, calls: list, concurrency: int, expected: tuple[str, ...] = ()) -> dict:
    """Run ``calls`` (each returns None when it worked, or a short label) on ``concurrency`` threads; labels in
    ``expected`` are normal answers, not errors."""
    latencies, errors, lock = [], Counter(), threading.Lock()

    def one(call):
        began = time.perf_counter()
        try:
            problem = call()
        except Exception as exc:  # noqa: BLE001 - counted, never hidden
            problem = type(exc).__name__
        elapsed = time.perf_counter() - began
        with lock:
            latencies.append(elapsed)
            if problem and problem not in expected:
                errors[problem] += 1

    began = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        list(pool.map(one, calls))
    wall = time.perf_counter() - began
    ms = [value * 1000 for value in latencies]
    result = {"scenario": name, "requests": len(calls), "concurrency": concurrency, "seconds": round(wall, 2),
              "per_second": round(len(calls) / wall, 1), "p50_ms": round(statistics.median(ms), 1),
              "p95_ms": round(percentile(ms, 0.95), 1), "p99_ms": round(percentile(ms, 0.99), 1),
              "max_ms": round(max(ms), 1), "errors": dict(errors)}
    print(f"  {name}: {result['per_second']}/s, p50 {result['p50_ms']} ms, p95 {result['p95_ms']} ms, "
          f"errors {result['errors'] or 0}", flush=True)
    return result


def expect(response: httpx.Response, status: int):
    return None if response.status_code == status else f"HTTP {response.status_code}"


# --- the scenarios ----------------------------------------------------------------------------------------------------

def run(args) -> list[dict]:
    url = checked_url(args.database_url)
    work = Path(tempfile.mkdtemp(prefix="reelforge-load-"))
    base = f"http://127.0.0.1:{args.port}"
    process = log = None
    try:
        print(f"installing a disposable API in {work} on {url.render_as_string(hide_password=True)}", flush=True)
        env = install(url, work)
        process, log = start_api(work, env, args.port)
        # The application modules of the copy, for seeding rows the HTTP API has no endpoint for.
        os.environ.update(REELFORGE_MASTER_KEY_FILE=env["REELFORGE_MASTER_KEY_FILE"])
        sys.path.insert(0, str(work))
        import app as copied  # noqa: PLC0415
        if not Path(copied.__file__).resolve().is_relative_to(work.resolve()):
            raise SystemExit("refusing to seed: the imported application is not the disposable copy")
        from app import jobs, notifications  # noqa: PLC0415
        from app.db import Session  # noqa: PLC0415
        from app.models import (CreditLedger, Membership, PaymentOrder, Plan, Project, Workflow, WorkflowJob,  # noqa: PLC0415
                                WorkflowRun, WorkflowRunStep)
        from sqlalchemy import func, select, update  # noqa: PLC0415

        admin = httpx.Client(base_url=base, timeout=60)
        response = admin.post("/api/setup", json={"email": "admin@example.com", "password": PASSWORD, "accept_terms": True},
                              headers=headers())
        assert response.status_code == 200, response.text
        results = []

        # Registration (scrypt hashing, a studio each): the accounts the next scenarios use.
        users = [f"user{index:03d}@example.com" for index in range(args.users)]
        results.append(measure("register", [
            (lambda email=email: expect(httpx.post(f"{base}/api/register", headers=headers(), timeout=60, json={
                "email": email, "password": PASSWORD, "workspace_name": email.split("@")[0], "accept_terms": True}), 201))
            for email in users], args.concurrency_login))

        # Sign-in: a fresh address each time, a few per account (the per-account limit is 10 in 15 minutes).
        logins = [email for email in users for _ in range(args.logins_per_user)]
        results.append(measure("login", [
            (lambda email=email: expect(httpx.post(f"{base}/api/login", headers=headers(), timeout=60,
                                                   json={"email": email, "password": PASSWORD}), 200))
            for email in logins], args.concurrency_login))

        sessions = []
        for email in users:
            client = httpx.Client(base_url=base, timeout=60, headers={"Origin": ORIGIN, "CF-Connecting-IP": address()})
            assert client.post("/api/login", json={"email": email, "password": PASSWORD}).status_code == 200
            sessions.append(client)
        rotation = itertools.cycle(sessions)

        # The dashboard every page loads (projects, workflows, assets, limits, account).
        for client in sessions:
            for index in range(3):
                client.post("/api/projects", json={"title": f"Project {index}", "topic": "load"})
        results.append(measure("dashboard", [
            (lambda client=next(rotation): expect(client.get("/api/dashboard"), 200)) for _ in range(args.requests)],
            args.concurrency))

        # Notifications: 50 per user; the unread count every open tab polls, and the list.
        with Session.begin() as db:
            for member in db.scalars(select(Membership)):
                for index in range(50):
                    notifications.notify(db, [member.user_id], "support.reply", "New support reply", f"Ticket {index}",
                                         workspace_id=member.workspace_id, params={"subject": f"Ticket {index}"})
        results.append(measure("notifications unread-count", [
            (lambda client=next(rotation): expect(client.get("/api/notifications/unread-count"), 200))
            for _ in range(args.requests)], args.concurrency))
        results.append(measure("notifications list", [
            (lambda client=next(rotation): expect(client.get("/api/notifications", params={"limit": 20}), 200))
            for _ in range(args.requests // 2)], args.concurrency))

        # Job claiming: the workers' SELECT … FOR UPDATE SKIP LOCKED, one job per claim, each job exactly once.
        owner = sessions[0]
        workflow_id = owner.post("/api/workflows", json={"name": "Load"}).json()["id"]
        now = datetime.now(timezone.utc)
        with Session.begin() as db:
            workspace_id = db.scalar(select(Workflow.workspace_id).where(Workflow.id == workflow_id))
            project_id = db.scalar(select(Project.id).where(Project.workspace_id == workspace_id))
            db.add(WorkflowRun(id="load-run", workspace_id=workspace_id, workflow_id=workflow_id, project_id=project_id,
                               graph_snapshot="{}", status="running", created_at=now))
            db.flush()
            db.add_all([WorkflowRunStep(id=f"load-step-{index}", run_id="load-run", node_id=f"n{index}", node_type="video",
                                        position=index, status="queued", detail="") for index in range(args.jobs)])
            db.flush()
            for index in range(args.jobs):
                jobs.enqueue_job(db, workspace_id=workspace_id, run_id="load-run", step_id=f"load-step-{index}",
                                 logical_key=f"load:{index}", payload={"index": index}, available_at=now)
        claimed, claim_lock = [], threading.Lock()

        def claim_until_empty(worker: int):
            def claim():
                with Session.begin() as db:
                    found = [job.id for job in jobs.claim_due_jobs(db, worker_id=f"load-worker-{worker}", limit=1,
                                                                    logical_key_prefix="load:")]
                with claim_lock:
                    claimed.extend(found)
                return None if found else "empty"
            return claim

        # Each worker claims until the queue is empty; "empty" answers (the last poll of each worker) are not errors.
        per_worker = args.jobs // args.workers + 2
        calls = [claim_until_empty(worker) for worker in range(args.workers) for _ in range(per_worker)]
        result = measure("job claim (SKIP LOCKED)", calls, args.workers, expected=("empty",))
        with Session() as db:
            duplicates = db.scalar(select(func.count()).select_from(WorkflowJob)
                                   .where(WorkflowJob.logical_key.like("load:%"), WorkflowJob.attempt_count != 1))
        result["check"] = (f"{len(claimed)} claims, {len(set(claimed))} distinct of {args.jobs} jobs, "
                           f"{duplicates} claimed twice")
        assert len(claimed) == len(set(claimed)) == args.jobs and duplicates == 0, result["check"]
        results.append(result)

        # Payment callbacks: payOS signs, the webhook verifies, settles once; payOS may deliver the same callback again.
        checksum = secrets.token_hex(32)
        configured = admin.put("/api/admin/payment-config/payos", headers=headers(), json={
            "enabled": True, "client_id": {"action": "replace", "value": str(uuid.uuid4())},
            "api_key": {"action": "replace", "value": str(uuid.uuid4())},
            "checksum_key": {"action": "replace", "value": checksum}})
        assert configured.status_code == 200, configured.text
        with Session.begin() as db:
            db.execute(update(Plan).where(Plan.code == "standard").values(price_vnd=199000, monthly_credits=1000))
            orders = []
            for index, member in enumerate(db.scalars(select(Membership).where(Membership.role == "owner"))):
                code = 7_000_000_000 + index
                db.add(PaymentOrder(id=str(uuid.uuid4()), workspace_id=member.workspace_id, plan_code="standard",
                                    provider="payos", order_code=code, amount_vnd=199000, credits_award=1000,
                                    status="pending", created_at=datetime.now(timezone.utc)))
                orders.append(code)
        from payos._crypto.provider import CryptoProvider  # noqa: PLC0415 - the SDK's own signing
        from payos.types.webhooks.webhook import WebhookData  # noqa: PLC0415

        def callback(code: int) -> bytes:
            data = WebhookData(order_code=code, amount=199000, description=f"RF{code}", account_number="0123456789",
                               reference=f"FT{code}", transaction_date_time="2026-10-02 10:00:00", currency="VND",
                               payment_link_id=uuid.uuid4().hex, code="00", desc="success")
            signed = dict(data.model_dump_camel_case())
            signature = CryptoProvider().create_signature_from_object(signed, checksum)
            return json.dumps({"code": "00", "desc": "success", "success": True, "data": signed,
                               "signature": signature}).encode()

        bodies = [callback(code) for code in orders for _ in range(args.callback_repeats)]
        results.append(measure("payment callback (payOS)", [
            (lambda body=body: expect(httpx.post(f"{base}/api/webhooks/payos", content=body, timeout=60,
                                                 headers={"Content-Type": "application/json"}), 200))
            for body in bodies], args.concurrency_login))
        with Session() as db:
            paid = db.scalar(select(func.count()).select_from(PaymentOrder)
                             .where(PaymentOrder.order_code.in_(orders), PaymentOrder.status == "paid"))
            credited = db.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.reason == "subscription"))
        results[-1]["check"] = f"{paid} of {len(orders)} orders paid, {credited} credit grants"
        assert paid == credited == len(orders), results[-1]["check"]
        for client in sessions:
            client.close()
        admin.close()
        return results
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
        if log is not None:
            log.close()
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


def markdown(results: list[dict]) -> str:
    lines = ["| Scenario | Requests | Concurrency | Per second | p50 ms | p95 ms | p99 ms | Max ms | Errors |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for row in results:
        errors = ", ".join(f"{name} × {count}" for name, count in row["errors"].items()) or "0"
        lines.append(f"| {row['scenario']} | {row['requests']} | {row['concurrency']} | {row['per_second']} | "
                     f"{row['p50_ms']} | {row['p95_ms']} | {row['p99_ms']} | {row['max_ms']} | {errors} |")
    checks = [f"* {row['scenario']}: {row['check']}" for row in results if row.get("check")]
    return "\n".join(lines + ([""] + checks if checks else []))


def main() -> int:
    parser = argparse.ArgumentParser(description="Basic load verification on a disposable PostgreSQL database")
    parser.add_argument("--database-url", required=True, help="an isolated PostgreSQL database (name contains 'test')")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--logins-per-user", type=int, default=2)
    parser.add_argument("--requests", type=int, default=1000, help="dashboard and unread-count requests")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--concurrency-login", type=int, default=10)
    parser.add_argument("--jobs", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--callback-repeats", type=int, default=3)
    parser.add_argument("--json", help="also write the results to this file")
    parser.add_argument("--keep", action="store_true", help="keep the temporary copy (and its api.log)")
    args = parser.parse_args()
    results = run(args)
    print()
    print(markdown(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
