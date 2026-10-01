"""Phase 26: legal acceptance, account data, onboarding and product polish (backend side).

* registration records the Terms of Service / Privacy Policy version and time; the current version can be accepted
  later by accounts created before it;
* /api/status tells the sign-in page whether email works (Forgot password) and the terms version;
* the first-steps checklist follows the studio's own data;
* a member left without a studio creates one (while registration is open);
* the plan is everyone's, the payment history only owners' and admins';
* the frontend ships /terms and /privacy templates marked for legal review, in every language.
"""
from pathlib import Path
import re
import unittest

try:
    from tests.studio_harness import run_program
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program

ROOT = Path(__file__).resolve().parents[1]

PRODUCT = r'''
from app import accounts
from app.models import AuditEvent, SystemSetting, User, WorkflowRun, WorkflowRunStep
ORIGIN = {"Origin": "http://testserver"}

def browser():
    return TestClient(app, headers=ORIGIN)

status = TestClient(app).get("/api/status").json()
assert status["terms_version"] == accounts.TERMS_VERSION and status["email_delivery"] is False, status

# The admin from setup accepted nothing yet; accepting records the current version.
assert client.get("/api/dashboard").json()["account"]["terms_accepted"] is False
assert client.post("/api/account/terms").json() == {"version": accounts.TERMS_VERSION}
assert client.get("/api/dashboard").json()["account"]["terms_accepted"] is True

# Onboarding: from the studio's own data, step by step.
member = browser()
assert member.post("/api/register", json={"email": "member@example.com", "password": "member-password-1",
                                          "workspace_name": "Member", "accept_terms": True}).status_code == 201
with Session() as db:
    stored = db.scalar(select(User).where(User.email == "member@example.com"))
    assert stored.terms_version == accounts.TERMS_VERSION and stored.terms_accepted_at is not None
steps = {step["key"]: step["done"] for step in member.get("/api/onboarding").json()["steps"]}
assert steps == {"project": False, "channel": False, "template": False, "generate": False, "review": False,
                 "publish": False}, steps
assert member.post("/api/projects", json={"title": "First", "topic": "t"}).status_code == 201
assert member.post("/api/workflows", json={"name": "Flow"}).status_code == 201
onboarding = member.get("/api/onboarding").json()
steps = {step["key"]: step["done"] for step in onboarding["steps"]}
assert steps["project"] and steps["template"] and not steps["generate"] and onboarding["complete"] is False
assert onboarding["role"] == "owner"

# A member without a studio creates one (registration open); an owner of one cannot create a second.
assert member.post("/api/workspaces", json={"name": "Second"}).json()["detail"] == "You already own a studio"
invite = member.post("/api/workspace/invites", json={"email": "guest@example.com", "role": "editor"}).json()
guest = browser()
assert guest.post("/api/register", json={"email": "guest@example.com", "password": "guest-password-12", "accept_terms": True,
                                         "invite_token": invite["link"].split("#token=")[1]}).status_code == 201
# The plan is everyone's; the payment history is not the editor's.
billing = guest.get("/api/billing").json()
assert billing["subscription"]["plan_code"] == "trial" and billing["orders"] == [] and billing["orders_total"] == 0
assert guest.get("/api/billing/orders").status_code == 403
assert member.get("/api/billing/orders").status_code == 200
assert guest.post("/api/workspace/leave").json() == {"ok": True}
assert guest.get("/api/dashboard").status_code == 403
with Session.begin() as db:
    db.get(SystemSetting, "registration_enabled").value = json.dumps(False)
assert guest.post("/api/workspaces", json={"name": "Mine"}).json()["detail"] == "Registration is closed"
with Session.begin() as db:
    db.get(SystemSetting, "registration_enabled").value = json.dumps(True)
created = guest.post("/api/workspaces", json={"name": "  Guest   Studio "})
assert created.status_code == 201 and created.json()["name"] == "Guest Studio", created.text
d = guest.get("/api/dashboard").json()
assert d["workspace"]["name"] == "Guest Studio" and d["workspace"]["role"] == "owner"
with Session() as db:
    assert "workspace.created" in {row.action for row in db.scalars(select(AuditEvent))}
print("ok")
'''


class ProductTest(unittest.TestCase):
    def test_terms_status_onboarding_own_studio_and_billing_visibility(self):
        result = run_program(PRODUCT)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])


class LegalPagesTest(unittest.TestCase):
    def test_terms_and_privacy_templates_exist_and_are_marked(self):
        app_dir = ROOT / "frontend" / "src" / "app"
        for page in ("terms", "privacy", "forgot-password", "reset-password", "verify-email", "invite"):
            self.assertTrue((app_dir / page / "page.tsx").is_file(), page)
        for locale in ("vi", "en", "ja"):
            text = (ROOT / "frontend" / "src" / "lib" / "i18n" / f"{locale}.ts").read_text(encoding="utf-8")
            legal = text[text.index("  legal: {"):text.index("  onboarding: {")]
            with self.subTest(locale=locale):
                self.assertGreaterEqual(len(re.findall(r"\[[^\]]{3,}\]", legal)), 8)  # placeholders to fill in
                self.assertIn("templateNotice", legal)
        providers = (app_dir / "providers.tsx").read_text(encoding="utf-8")
        for path in ("/forgot-password", "/reset-password", "/verify-email", "/invite", "/terms", "/privacy"):
            self.assertIn(f'"{path}"', providers)

    def test_security_headers_on_pages(self):
        config = (ROOT / "frontend" / "next.config.ts").read_text(encoding="utf-8")
        for needle in ("frame-ancestors 'none'", "object-src 'none'", "Strict-Transport-Security", "X-Content-Type-Options",
                       "Referrer-Policy", "Permissions-Policy", '"/((?!api/).*)"'):
            self.assertIn(needle, config)


if __name__ == "__main__":
    unittest.main()
