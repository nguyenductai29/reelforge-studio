"""Phase 24: production security and observability.

* the client address behind the trusted proxy (CF-Connecting-IP only from loopback; X-Forwarded-For never);
* cross-site request protection: Origin/Referer required with the session cookie, webhooks exempt;
* security headers, HSTS with an HTTPS public origin;
* stable error bodies with a request ID; no stack trace, secret or submitted value in an error;
* /health/live and /health/ready (database, migrations, master key);
* /internal/metrics for loopback or a system admin, without personal data in labels;
* the audit log for admins; system alerts sent once per condition and cooldown.
"""
import unittest
from unittest.mock import patch

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

from app import client_ip, http_security, metrics


def scope(peer, headers=()):
    return {"type": "http", "client": (peer, 5000) if peer else None,
            "headers": [(name.lower().encode(), value.encode()) for name, value in headers]}


class ClientAddressTest(unittest.TestCase):
    def test_header_trusted_only_from_the_proxy(self):
        resolve = client_ip.resolve_scope
        self.assertEqual(resolve(scope("127.0.0.1", [("CF-Connecting-IP", "203.0.113.5")])), "203.0.113.5")
        self.assertEqual(resolve(scope("::1", [("cf-connecting-ip", "2001:db8::7")])), "2001:db8::7")
        self.assertEqual(resolve(scope("198.51.100.1", [("CF-Connecting-IP", "203.0.113.5")])), "198.51.100.1")
        self.assertEqual(resolve(scope("127.0.0.1", [("CF-Connecting-IP", "not-an-address")])), "127.0.0.1")
        self.assertEqual(resolve(scope("127.0.0.1", [("X-Forwarded-For", "203.0.113.9")])), "127.0.0.1")
        self.assertEqual(resolve(scope("127.0.0.1")), "127.0.0.1")
        self.assertEqual(resolve(scope(None)), "unknown")
        with patch.object(client_ip, "header_name", lambda: ""):  # header trust turned off
            self.assertEqual(resolve(scope("127.0.0.1", [("CF-Connecting-IP", "203.0.113.5")])), "127.0.0.1")
        with patch.object(client_ip, "trusted_networks", lambda: client_ip._networks("10.0.0.0/8")):
            self.assertEqual(resolve(scope("10.1.2.3", [("CF-Connecting-IP", "203.0.113.5")])), "203.0.113.5")
            self.assertEqual(resolve(scope("127.0.0.1", [("CF-Connecting-IP", "203.0.113.5")])), "127.0.0.1")


class OriginTest(unittest.TestCase):
    def test_origin_and_referer(self):
        allowed = {"https://studio.example.org"}
        base = [("Host", "127.0.0.1:8000")]
        ok = http_security.origin_ok
        self.assertTrue(ok(scope("127.0.0.1", [*base, ("Origin", "https://studio.example.org")]), allowed, require=True))
        self.assertTrue(ok(scope("127.0.0.1", [*base, ("Referer", "https://studio.example.org/settings?tab=x")]),
                           allowed, require=True))
        self.assertFalse(ok(scope("127.0.0.1", [*base, ("Referer", "https://evil.example.com/")]), allowed, require=True))
        self.assertFalse(ok(scope("127.0.0.1", [*base, ("Origin", "null")]), allowed, require=False))
        self.assertFalse(ok(scope("127.0.0.1", base), allowed, require=True))
        self.assertTrue(ok(scope("127.0.0.1", base), allowed, require=False))
        self.assertTrue(ok(scope("127.0.0.1", [*base, ("Origin", "http://127.0.0.1:8000")]), allowed, require=True))


class MetricsFormatTest(unittest.TestCase):
    def test_labels_are_bounded(self):
        metrics.observe_http("GET", "/api/projects/{project_id}", 200, 0.02)
        metrics.inc("login_failures", reason="invalid")
        text = metrics.render()
        self.assertIn('reelforge_http_requests_total{method="GET",route="/api/projects/{project_id}",status="200"}', text)
        self.assertIn('reelforge_http_request_duration_seconds_bucket{method="GET",route="/api/projects/{project_id}",le="0.05"}', text)
        self.assertIn('reelforge_login_failures_total{reason="invalid"}', text)


OBSERVABILITY = r'''
import os
from app import alerts, health, heartbeat, logs, metrics as metrics_module, storage
logs.configure_logging()  # JSON lines, as in production (TestClient does not run the lifespan)
from app.models import AuditEvent, BackupRun, Notification, SystemAlert, User, WorkerHeartbeat
ORIGIN = {"Origin": "http://testserver"}

# --- cross-site requests -------------------------------------------------------------------------------------------
bare = TestClient(app)  # no Origin header, like a script
for name, value in client.cookies.items():
    bare.cookies.set(name, value)
r = bare.post("/api/projects", json={"title": "x"})
assert r.status_code == 403 and r.json()["code"] == "invalid_origin", r.text  # session cookie, no Origin
assert bare.post("/api/projects", json={"title": "x"}, headers={"Referer": "http://testserver/projects"}).status_code == 201
assert bare.post("/api/projects", json={"title": "x"}, headers={"Referer": "https://evil.example.com/"}).status_code == 403
assert client.post("/api/projects", json={"title": "x"}, headers={"Origin": "https://evil.example.com"}).status_code == 403
assert TestClient(app).post("/api/login", json={"email": "a@example.com", "password": "x" * 12}).status_code == 401
assert TestClient(app).post("/api/webhooks/payos", content=b"{}").status_code == 400  # signatures, not origins

# --- headers ---------------------------------------------------------------------------------------------------------------
r = client.get("/api/dashboard")
for name, value in (("x-content-type-options", "nosniff"), ("referrer-policy", "strict-origin-when-cross-origin"),
                    ("x-frame-options", "DENY"), ("cross-origin-opener-policy", "same-origin")):
    assert r.headers.get(name) == value, (name, r.headers.get(name))
assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and "camera=()" in r.headers["permissions-policy"]
assert "strict-transport-security" not in r.headers  # the public origin is plain HTTP here
assert len(r.headers["x-request-id"]) == 32
main.public_origin_is_https = lambda: True
assert client.get("/api/dashboard").headers["strict-transport-security"] == "max-age=31536000"
main.public_origin_is_https = lambda: False

# --- errors ----------------------------------------------------------------------------------------------------------------
r = client.get("/api/does-not-exist")
assert r.status_code == 404 and r.json()["code"] == "not_found" and r.json()["request_id"] == r.headers["x-request-id"]
r = client.post("/api/account/password", json={"current_password": {"x": "SUBMITTED-SECRET"}, "new_password": "a",
                                               "confirm_password": "a"})
assert r.status_code == 422 and r.json()["code"] == "validation_error", r.text
assert "SUBMITTED-SECRET" not in r.text and all(set(item) == {"loc", "msg", "type"} for item in r.json()["detail"])
original = main.storage.usage
def broken(*args, **kwargs):
    raise RuntimeError("boom with SECRET-INSIDE")
main.storage.usage = broken
crashing = TestClient(app, headers=ORIGIN, raise_server_exceptions=False)
for name, value in client.cookies.items():
    crashing.cookies.set(name, value)
r = crashing.get("/api/storage")
main.storage.usage = original
assert r.status_code == 500 and r.json()["detail"] == "Internal server error" and r.json()["code"] == "internal_error"
assert "SECRET-INSIDE" not in r.text and "Traceback" not in r.text and "boom" not in r.text
assert r.headers["x-request-id"] == r.json()["request_id"] and r.headers["x-content-type-options"] == "nosniff"
print("crash-request-id", r.json()["request_id"])

# --- the client address and request IDs behind the trusted proxy --------------------------------------------------------
proxy = TestClient(app, headers=ORIGIN, client=("127.0.0.1", 40000))
r = proxy.post("/api/login", json={"email": "owner@example.com", "password": "long-password-123"},
               headers={"CF-Connecting-IP": "203.0.113.77", "X-Request-ID": "cloudflare-ray-1234"})
assert r.status_code == 200 and r.headers["x-request-id"] == "cloudflare-ray-1234", r.headers
sessions = proxy.get("/api/account/security", headers={"CF-Connecting-IP": "203.0.113.77"}).json()["sessions"]
assert next(s for s in sessions if s["current"])["ip"] == "203.0.113.77", sessions
outsider = TestClient(app, headers=ORIGIN, client=("198.51.100.20", 40000))
r = outsider.post("/api/login", json={"email": "owner@example.com", "password": "long-password-123"},
                  headers={"CF-Connecting-IP": "203.0.113.78", "X-Request-ID": "forged-request-id-1"})
assert r.headers["x-request-id"] != "forged-request-id-1"
with Session() as db:
    ips = {event.ip for event in db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login"))}
    assert "203.0.113.77" in ips and "198.51.100.20" in ips and "203.0.113.78" not in ips, ips

# --- health ---------------------------------------------------------------------------------------------------------------
assert client.get("/health/live").json() == {"status": "ok"}
ready = client.get("/health/ready")
assert ready.status_code == 200 and ready.json()["status"] == "ok", ready.text
assert set(ready.json()["checks"]) == {"database", "migrations", "master_key"}
saved = os.environ.get("REELFORGE_MASTER_KEY_FILE")
os.environ["REELFORGE_MASTER_KEY_FILE"] = str(Path("instance") / "absent.key")
r = client.get("/health/ready")
assert r.status_code == 503 and r.json()["checks"]["master_key"]["problem"] == "missing", r.text
os.environ.pop("REELFORGE_MASTER_KEY_FILE")
if saved:
    os.environ["REELFORGE_MASTER_KEY_FILE"] = saved
head = health.migration_head
health.migration_head = lambda: "9999_future"
r = client.get("/health/ready")
assert r.status_code == 503 and r.json()["checks"]["migrations"]["status"] == "behind", r.text
health.migration_head = head

# --- metrics ---------------------------------------------------------------------------------------------------------------
member = TestClient(app, headers=ORIGIN)
assert member.post("/api/register", json={"email": "member@example.com", "password": "member-password-1",
                                          "workspace_name": "Member", "accept_terms": True}).status_code == 201
assert member.get("/internal/metrics").status_code == 403  # neither loopback nor a system admin
assert TestClient(app).get("/internal/metrics").status_code == 401
local = TestClient(app, client=("127.0.0.1", 9100)).get("/internal/metrics")
assert local.status_code == 200 and local.headers["content-type"].startswith("text/plain"), local.text[:200]
admin_view = client.get("/internal/metrics").text
for name in ("reelforge_http_requests_total", "reelforge_jobs", "reelforge_worker_heartbeat_age_seconds",
             "reelforge_workflow_runs", "reelforge_payments_waiting", "reelforge_media_stored_bytes",
             "reelforge_backup_age_seconds", "reelforge_login_failures_24h", "reelforge_sse_connections",
             "reelforge_up 1"):
    assert name in admin_view, name
assert "@" not in admin_view and "My Studio" not in admin_view

# --- the audit log ------------------------------------------------------------------------------------------------------------
page = client.get("/api/admin/audit", params={"action": "auth.", "limit": 2}).json()
assert page["total"] >= 3 and len(page["items"]) == 2 and page["items"][0]["at"] >= page["items"][1]["at"]
assert client.get("/api/admin/audit", params={"actor": "member@"}).json()["items"][0]["actor"]["email"] == "member@example.com"
assert member.get("/api/admin/audit").status_code == 403

# --- alerts: once per condition, again only after the cooldown, resolved silently ----------------------------------------------
now = datetime.now(timezone.utc)
with Session.begin() as db:
    db.add(WorkerHeartbeat(worker="video_worker", host="h", pid=1, status="running", started_at=now - timedelta(hours=3),
                           last_seen_at=now - timedelta(hours=2)))
    db.add(BackupRun(kind="database", status="failed", started_at=now - timedelta(hours=1), error="pg_dump failed"))
for _ in range(3):
    assert TestClient(app).post("/api/webhooks/payos", content=b"{}").status_code == 400
original_disk = storage.disk_usage
storage.disk_usage = lambda db: {"total_bytes": 100, "used_bytes": 95, "free_bytes": 5, "percent": 95}
with Session.begin() as db:
    first = alerts.evaluate(db, now)
with Session() as db:
    active = {row["key"]: row["level"] for row in alerts.active(db)}
    sent = db.scalars(select(Notification).where(Notification.type == "system.alert")).all()
assert {"worker:video_worker", "backup:failed", "backup:overdue", "disk:media", "payments:callbacks"} <= set(active), active
assert active["disk:media"] == "critical" and first["notified"] == len(active), (first, active)
assert len(sent) == len(active)
with Session.begin() as db:
    assert alerts.evaluate(db, now + timedelta(minutes=10))["notified"] == 0  # cooldown
with Session.begin() as db:
    later = alerts.evaluate(db, now + timedelta(hours=13))
with Session() as db:
    still = {row["key"] for row in alerts.active(db)}
# After the cooldown each lasting condition notifies again; the callbacks counted in the last hour are gone.
assert later["notified"] == len(still) and "payments:callbacks" not in still and "disk:media" in still, (later, still)
storage.disk_usage = original_disk
with Session.begin() as db:
    db.get(WorkerHeartbeat, "video_worker").last_seen_at = datetime.now(timezone.utc)
with Session.begin() as db:
    alerts.evaluate(db, datetime.now(timezone.utc))
with Session() as db:
    remaining = {row["key"] for row in alerts.active(db)}
    resolved = db.get(SystemAlert, "worker:video_worker")
assert "worker:video_worker" not in remaining and "disk:media" not in remaining and resolved.resolved_at, remaining
readiness_view = client.get("/api/admin/readiness").json()
sections = {section["key"]: section["checks"] for section in readiness_view["sections"]}
assert {"backups", "email", "accounts", "alerts"} <= set(sections), sections.keys()
assert any(check["detail"] == "backup:failed" for check in sections["alerts"]), sections["alerts"]
print("ok")
'''


class ObservabilityTest(unittest.TestCase):
    def test_csrf_headers_errors_health_metrics_audit_alerts(self):
        result = run_program(OBSERVABILITY)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])
        request_id = next(line.split()[1] for line in result.stdout.splitlines() if line.startswith("crash-request-id"))
        # The structured log line of the crash names the request ID, never the exception message.
        crash = [line for line in result.stderr.splitlines() if '"unhandled_error"' in line]
        self.assertTrue(crash, result.stderr[-3000:])
        self.assertIn(request_id, crash[0])
        self.assertNotIn("SECRET-INSIDE", crash[0])


if __name__ == "__main__":
    unittest.main()
