"""Phase 22: account security and transactional email.

* TOTP (RFC 6238 vectors, drift window, replay), recovery codes;
* email templates in vi/en/ja with a plain-text part; the SMTP adapter against a local sink, the Resend adapter
  against a mock transport; error codes, never a secret;
* the outbox: queued in the request's transaction, encrypted, erased once sent, retried with back-off;
* registration (terms acceptance), email verification (single use, resend rate limit), welcome email;
* forgot / reset password (the same answer whether the account exists, single-use 60-minute token, every session
  revoked, notification), change password (other sessions revoked, this one kept);
* optional TOTP 2FA: enrollment with one correct code, the second sign-in step, five tries per challenge, replay
  refused, recovery codes once, disable with password and code, admin reset;
* sessions: listed without tokens, revoked one by one or all others;
* audit events for each, without a password, token or code; account export and closure request;
* migration 0022: existing accounts verified, existing sessions keep working.

Offline: no email leaves the machine (a capture hook, a loopback SMTP sink, httpx.MockTransport).
"""
import email
from email import policy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import httpx

try:
    from tests.studio_harness import run_program
    from tests.smtp_sink import SMTPSink
except ModuleNotFoundError:  # run from inside tests/
    from studio_harness import run_program
    from smtp_sink import SMTPSink

from app import accounts, email_templates, mailer, totp

ROOT = Path(__file__).resolve().parents[1]


class TOTPTest(unittest.TestCase):
    # RFC 6238 appendix B: the ASCII secret "12345678901234567890", SHA-1, last six of the eight digits.
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def test_rfc_6238_vectors(self):
        for at, expected in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
                             (1234567890, "005924"), (2000000000, "279037")):
            with self.subTest(at=at):
                self.assertEqual(totp.code_at(self.SECRET, totp.step_at(at)), expected)
                self.assertEqual(totp.verify(self.SECRET, expected, at=at), totp.step_at(at))

    def test_window_replay_and_format(self):
        at = 1234567890
        step = totp.step_at(at)
        previous = totp.code_at(self.SECRET, step - 1)
        self.assertEqual(totp.verify(self.SECRET, previous, at=at), step - 1)  # one step of clock drift
        self.assertIsNone(totp.verify(self.SECRET, totp.code_at(self.SECRET, step - 2), at=at))
        self.assertIsNone(totp.verify(self.SECRET, "005924", at=at, last_step=step))  # already used
        self.assertEqual(totp.verify(self.SECRET, "005 924", at=at), step)
        self.assertIsNone(totp.verify(self.SECRET, "12345", at=at))
        self.assertIsNone(totp.verify(self.SECRET, "abcdef", at=at))
        secret = totp.new_secret()
        self.assertEqual(len(secret), 32)
        uri = totp.provisioning_uri(secret, "a@example.com")
        self.assertTrue(uri.startswith("otpauth://totp/ReelForge%20Studio:a@example.com?secret="), uri)
        self.assertTrue(totp.qr_data_uri(uri).startswith("data:image/svg+xml"))

    def test_recovery_codes(self):
        codes = totp.new_recovery_codes()
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        for code in codes:
            self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
            self.assertFalse(totp.is_totp_code(code))
            self.assertEqual(totp.recovery_digest("u", code), totp.recovery_digest("u", code.lower().replace("-", " ")))
            self.assertNotEqual(totp.recovery_digest("u", code), totp.recovery_digest("v", code))
        self.assertTrue(totp.is_totp_code("123 456"))

    def test_password_rules(self):
        self.assertEqual(accounts.password_problem("short", "a@b.c"), "too_short")
        self.assertEqual(accounts.password_problem("x" * 257, "a@b.c"), "too_long")
        self.assertEqual(accounts.password_problem("Someone@Example.com", "someone@example.com"), "same_as_email")
        self.assertIsNone(accounts.password_problem("a long enough password", "a@b.c"))


SAMPLE = {"link": "https://studio.example/x#token=abc", "hours": 24, "minutes": 60, "time": "2026-10-02T14:05:00+00:00",
          "subject": "<Help> & thanks", "plan": "Standard", "amount": 199000, "reference": "1234567890123",
          "ends": "2026-11-01T00:00:00+00:00", "credits": 100, "reason": "Not found", "workspace": "Studio <b>",
          "ip": "203.0.113.9", "remaining": 9, "inviter": "owner@example.com", "role": "editor", "days": 7}


class TemplateTest(unittest.TestCase):
    def test_every_template_in_every_language_has_text_and_html(self):
        for name in email_templates.NAMES:
            for locale in email_templates.LOCALES:
                with self.subTest(template=name, locale=locale):
                    subject, text, html = email_templates.render(name, locale, SAMPLE)
                    self.assertTrue(subject.strip())
                    self.assertNotIn("{", subject + text)
                    self.assertIn("ReelForge Studio", html)
                    self.assertNotIn("Studio <b>", html)  # parameters are escaped in the HTML part
                    self.assertNotIn("<Help>", html)
                    if email_templates.TEMPLATES[name][locale][2]:
                        self.assertIn(SAMPLE["link"], text)
                        self.assertIn("https://studio.example/x#token=abc", html)

    def test_formats_follow_the_language(self):
        _, text_vi, _ = email_templates.render("payment_succeeded", "vi", SAMPLE)
        _, text_en, _ = email_templates.render("payment_succeeded", "en", SAMPLE)
        self.assertIn("199.000 ₫", text_vi)
        self.assertIn("199,000 VND", text_en)
        self.assertIn("2026-11-01 00:00 UTC", text_en)
        _, text_ja, _ = email_templates.render("member_invite", "ja", SAMPLE)
        self.assertIn("編集者", text_ja)
        # An unknown language falls back to Vietnamese.
        self.assertEqual(email_templates.render("test", "fr", SAMPLE)[0], email_templates.render("test", "vi", SAMPLE)[0])


CFG = {"enabled": True, "provider": "smtp", "from_name": "ReelForge Studio", "from_email": "studio@example.com",
       "reply_to": "help@example.com", "smtp_host": "127.0.0.1", "smtp_port": 0, "smtp_username": "",
       "smtp_password": "", "smtp_security": "none", "resend_api_key": "re_SENTINEL_KEY"}


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.sink = SMTPSink().start()
        self.cfg = {**CFG, "smtp_port": self.sink.port}
        self.message = mailer.build("password_reset", "en", SAMPLE, "user@example.com", self.cfg)

    def tearDown(self):
        self.sink.stop()

    def test_smtp_sends_text_and_html_with_auth(self):
        mailer._smtp_send({**self.cfg, "smtp_username": "mailer", "smtp_password": "smtp-SENTINEL"}, self.message)
        self.assertEqual(self.sink.logins, [("mailer", "smtp-SENTINEL")])
        mail_from, rcpt, raw = self.sink.messages[0]
        self.assertEqual((mail_from, rcpt), ("studio@example.com", ["user@example.com"]))
        parsed = email.message_from_bytes(raw, policy=policy.default)
        self.assertEqual(parsed["Subject"], "Reset your password – ReelForge Studio")
        self.assertEqual(parsed["Reply-To"], "help@example.com")
        self.assertIn("ReelForge Studio", parsed["From"])
        self.assertIn(SAMPLE["link"], parsed.get_body(("plain",)).get_content())
        self.assertIn("<a href=", parsed.get_body(("html",)).get_content())
        self.assertNotIn(b"smtp-SENTINEL", raw)

    def test_smtp_errors_become_codes(self):
        self.sink.reject_password = "wrong-SENTINEL"
        with self.assertRaises(mailer.EmailError) as raised:
            mailer._smtp_send({**self.cfg, "smtp_username": "u", "smtp_password": "wrong-SENTINEL"}, self.message)
        self.assertEqual(raised.exception.code, "auth_failed")
        self.assertNotIn("SENTINEL", str(raised.exception))
        self.sink.reject_recipients.add("user@example.com")
        with self.assertRaises(mailer.EmailError) as raised:
            mailer._smtp_send(self.cfg, self.message)
        self.assertEqual(raised.exception.code, "rejected")
        with self.assertRaises(mailer.EmailError) as raised:
            mailer._smtp_send({**self.cfg, "smtp_port": 1}, self.message)
        self.assertIn(raised.exception.code, ("connection_failed", "timeout"))

    def test_resend_adapter(self):
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, json={"id": "email_1"})

        mailer.RESEND_TRANSPORT = httpx.MockTransport(handle)
        try:
            mailer._resend_send({**self.cfg, "provider": "resend"}, self.message)
            body = json.loads(seen[0].content)
            self.assertEqual(seen[0].headers["authorization"], "Bearer re_SENTINEL_KEY")
            self.assertEqual(body["to"], ["user@example.com"])
            self.assertEqual(body["reply_to"], "help@example.com")
            self.assertIn("html", body)
            self.assertIn("text", body)
            for status, code in ((401, "auth_failed"), (422, "rejected"), (500, "provider_error")):
                mailer.RESEND_TRANSPORT = httpx.MockTransport(lambda request, status=status: httpx.Response(status))
                with self.assertRaises(mailer.EmailError) as raised:
                    mailer._resend_send({**self.cfg, "provider": "resend"}, self.message)
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn("SENTINEL", str(raised.exception))
        finally:
            mailer.RESEND_TRANSPORT = None

    def test_configuration_problems(self):
        self.assertEqual(mailer.provider_problem({**self.cfg, "from_email": ""}), "from_missing")
        self.assertEqual(mailer.provider_problem({**self.cfg, "smtp_host": ""}), "smtp_host_missing")
        self.assertEqual(mailer.provider_problem({**self.cfg, "provider": "resend", "resend_api_key": ""}),
                         "resend_key_missing")
        self.assertEqual(mailer.problem({**self.cfg, "enabled": False}), "disabled")


ACCOUNTS = r'''
import hashlib, re
from sqlalchemy import delete
from app import accounts, audit as audit_log, mailer, ratelimit, totp
from app.models import (AccountToken, AuditEvent, EmailOutbox, LoginSession, RateLimitBucket, RecoveryCode,
                        SupportTicket, User)
# The flows below sign in and enter codes far more often than a person would; the limits are checked at the end.
DEFAULT_LIMITS = dict(ratelimit.LIMITS)
ratelimit.LIMITS.update({"login_ip": (1000, 900), "login_account": (1000, 900), "two_factor_account": (1000, 900),
                         "account_change": (1000, 900)})
mailer.AUTO_DELIVER = False
SENT = []
mailer.SEND_OVERRIDE = lambda message: SENT.append(message)
with Session.begin() as db:
    system_config.save(db, None, values={"email.enabled": True, "email.provider": "smtp",
                                         "email.from_email": "studio@example.com", "email.smtp.host": "127.0.0.1"},
                       section="email")
ORIGIN = {"Origin": "http://testserver"}

def browser():
    return TestClient(app, headers=ORIGIN)

def mails(to):
    mailer.deliver_pending()
    return [message for message in SENT if message.to == to]

def token_in(message):
    return re.search(r"#token=([A-Za-z0-9_-]+)", message.text).group(1)

def user(email):
    from sqlalchemy.orm import undefer
    with Session() as db:
        return db.scalar(select(User).options(undefer("*")).where(User.email == email))

SECRETS_SEEN = []

# --- registration, terms and email verification ------------------------------------------------------------------
guest = browser()
body = {"email": "new@example.com", "password": "first-password-11", "workspace_name": "New", "locale": "en"}
r = guest.post("/api/register", json=body)
assert r.status_code == 400 and r.json()["detail"] == "Accept the Terms of Service and Privacy Policy", r.text
r = guest.post("/api/register", json={**body, "accept_terms": True})
assert r.status_code == 201, r.text
account = user("new@example.com")
assert account.terms_version == accounts.TERMS_VERSION and account.terms_accepted_at and account.locale == "en"
assert account.email_verified_at is None and account.created_at and account.password_changed_at
assert guest.get("/api/dashboard").json()["account"] == {"email_verified": False, "two_factor_enabled": False,
                                                          "terms_accepted": True, "email_delivery": True}
verify = mails("new@example.com")
assert [m.subject for m in verify] == ["Verify your email – ReelForge Studio"], [m.subject for m in verify]
assert verify[0].html and "#token=" in verify[0].text
raw = token_in(verify[0])
SECRETS_SEEN.append(raw)
with Session() as db:
    row = db.scalar(select(EmailOutbox).where(EmailOutbox.to_address == "new@example.com"))
    assert row.status == "sent" and row.payload_ciphertext is None and row.sent_at  # the link is erased once sent
    tokens = db.scalars(select(AccountToken).where(AccountToken.purpose == "verify_email")).all()
    assert [t.token_hash for t in tokens] == [hashlib.sha256(raw.encode()).hexdigest()]
anonymous = browser()
assert anonymous.post("/api/account/verify-email", json={"token": raw}).json() == {"verified": True,
                                                                                    "email": "new@example.com"}
assert anonymous.post("/api/account/verify-email", json={"token": raw}).status_code == 410
assert guest.get("/api/dashboard").json()["account"]["email_verified"] is True
assert [m.subject for m in mails("new@example.com")][-1] == "Welcome to ReelForge Studio"
assert guest.post("/api/account/verify-email/resend").json() == {"sent": False, "verified": True}

late = browser()
assert late.post("/api/register", json={"email": "late@example.com", "password": "late-password-11",
                                        "workspace_name": "Late", "accept_terms": True}).status_code == 201
for _ in range(3):
    assert late.post("/api/account/verify-email/resend").json() == {"sent": True, "verified": False}
r = late.post("/api/account/verify-email/resend")
assert r.status_code == 429 and r.headers["retry-after"] and r.json()["code"] == "rate_limited", r.text
assert len(mails("late@example.com")) == 4  # registration + three resends; only the newest link works
links = [token_in(m) for m in mails("late@example.com")]
assert anonymous.post("/api/account/verify-email", json={"token": links[0]}).status_code == 410
assert anonymous.post("/api/account/verify-email", json={"token": links[-1]}).status_code == 200

# --- forgot and reset password ------------------------------------------------------------------------------------
mailer.deliver_pending()  # late@'s welcome email, queued by its verification
before = len(SENT)
unknown = anonymous.post("/api/account/password/forgot", json={"email": "nobody@example.com"})
known = anonymous.post("/api/account/password/forgot", json={"email": "New@Example.com"})
assert unknown.status_code == known.status_code == 200 and unknown.json() == known.json() == {"ok": True}
mailer.deliver_pending()
assert [m.to for m in SENT[before:]] == ["new@example.com"], [m.to for m in SENT[before:]]
reset = SENT[-1]
assert reset.subject == "Reset your password – ReelForge Studio" and "/reset-password#token=" in reset.text
raw = token_in(reset)
SECRETS_SEEN.append(raw)
assert anonymous.post("/api/account/password/reset/check", json={"token": raw}).json() == {"valid": True}
r = anonymous.post("/api/account/password/reset", json={"token": raw, "password": "short"})
assert r.status_code == 400, r.text
r = anonymous.post("/api/account/password/reset", json={"token": raw, "password": "second-password-22",
                                                        "confirm_password": "something-else-22"})
assert r.status_code == 400 and r.json()["detail"] == "Passwords do not match"
assert anonymous.post("/api/account/password/reset", json={"token": raw, "password": "second-password-22"}).json() == {"ok": True}
assert guest.get("/api/dashboard").status_code == 401  # every session of the account is signed out
assert anonymous.post("/api/account/password/reset", json={"token": raw, "password": "third-password-33"}).status_code == 410
assert anonymous.post("/api/account/password/reset/check", json={"token": raw}).json() == {"valid": False}
assert browser().post("/api/login", json={"email": "new@example.com", "password": "first-password-11"}).status_code == 401
assert mails("new@example.com")[-1].subject == "Your password was changed"
with Session.begin() as db:
    expired = accounts.issue_token(db, db.scalar(select(User).where(User.email == "new@example.com")), "password_reset",
                                   lifetime=accounts.RESET_LIFETIME)
    db.flush()
    db.execute(update(AccountToken).where(AccountToken.token_hash == hashlib.sha256(expired.encode()).hexdigest())
               .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
assert anonymous.post("/api/account/password/reset", json={"token": expired, "password": "fourth-password-44"}).status_code == 410

# --- sessions and change password -----------------------------------------------------------------------------------
PASSWORD = "second-password-22"
def signed_in(agent):
    c = TestClient(app, headers={**ORIGIN, "user-agent": agent})
    r = c.post("/api/login", json={"email": "new@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["two_factor_required"] is False, r.text
    return c
first, second = signed_in("Browser A"), signed_in("Browser B")
view = first.get("/api/account/security").json()
sessions = view["sessions"]
assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1, sessions
assert all(set(s) == {"id", "user_agent", "ip", "created_at", "last_seen_at", "expires_at", "current"} for s in sessions)
assert {s["user_agent"] for s in sessions} == {"Browser A", "Browser B"}
other = next(s for s in sessions if not s["current"])
assert first.delete(f"/api/account/sessions/{other['id']}").status_code == 204
assert second.get("/api/dashboard").status_code == 401 and first.get("/api/dashboard").status_code == 200
assert first.delete(f"/api/account/sessions/{other['id']}").status_code == 404
second = signed_in("Browser B")
assert first.post("/api/account/sessions/revoke-others").json() == {"revoked": 1}
assert second.get("/api/dashboard").status_code == 401 and first.get("/api/dashboard").status_code == 200
second = signed_in("Browser B")
change = {"current_password": "wrong-password-00", "new_password": "third-password-33", "confirm_password": "third-password-33"}
r = first.post("/api/account/password", json=change)
assert r.status_code == 400 and r.json()["detail"] == "Current password is incorrect"
r = first.post("/api/account/password", json={**change, "current_password": PASSWORD, "confirm_password": "x" * 14})
assert r.status_code == 400 and r.json()["detail"] == "Passwords do not match"
r = first.post("/api/account/password", json={**change, "current_password": PASSWORD})
assert r.status_code == 200 and r.json()["sessions_revoked"] == 1, r.text
assert first.get("/api/dashboard").status_code == 200 and second.get("/api/dashboard").status_code == 401
PASSWORD = "third-password-33"
assert mails("new@example.com")[-1].subject == "Your password was changed"

# --- two-factor authentication ------------------------------------------------------------------------------------------
second = signed_in("Browser B")
assert first.post("/api/account/2fa/setup", json={"password": "wrong-password-00"}).status_code == 400
setup = first.post("/api/account/2fa/setup", json={"password": PASSWORD}).json()
secret = setup["secret"]
SECRETS_SEEN.append(secret)
assert setup["uri"].startswith("otpauth://totp/") and setup["qr"].startswith("data:image/svg+xml")
with Session() as db:
    stored = db.scalar(select(User).where(User.email == "new@example.com"))
    assert stored.totp_pending_ciphertext and secret not in stored.totp_pending_ciphertext and not stored.totp_enabled_at
assert first.post("/api/account/2fa/enable", json={"code": "000000"}).status_code == 400
code = totp.code_at(secret, totp.step_at())
enabled = first.post("/api/account/2fa/enable", json={"code": code}).json()
codes = enabled["recovery_codes"]
SECRETS_SEEN.extend(codes)
assert enabled["enabled"] is True and len(codes) == 10
assert second.get("/api/dashboard").status_code == 401  # enabling 2FA signs the other sessions out
assert first.get("/api/dashboard").json()["account"]["two_factor_enabled"] is True
with Session() as db:
    stored = db.scalar(select(User).where(User.email == "new@example.com"))
    assert stored.totp_ciphertext and secret not in stored.totp_ciphertext and stored.totp_pending_ciphertext is None
    hashes = {row.code_hash for row in db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == stored.id))}
    assert len(hashes) == 10 and not hashes & set(codes)
assert first.get("/api/account/security").json()["two_factor"]["recovery_codes_remaining"] == 10
assert mails("new@example.com")[-1].subject == "Two-factor authentication turned on"

def password_step():
    c = browser()
    r = c.post("/api/login", json={"email": "new@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json() == {"email": "new@example.com", "two_factor_required": True}, r.text
    assert "rf_session" not in c.cookies and c.cookies.get("rf_challenge")
    assert c.get("/api/dashboard").status_code == 401
    return c
c = password_step()
assert c.post("/api/login/2fa", json={"code": "000000"}).status_code == 401
assert c.post("/api/login/2fa", json={"code": code}).status_code == 401  # the enrollment code cannot be replayed
step = totp.step_at()
r = c.post("/api/login/2fa", json={"code": totp.code_at(secret, step + 1)})
assert r.status_code == 200, r.text
assert c.get("/api/dashboard").status_code == 200 and not c.cookies.get("rf_challenge")
c = password_step()
for _ in range(5):
    assert c.post("/api/login/2fa", json={"code": "111111"}).status_code == 401
r = c.post("/api/login/2fa", json={"code": totp.code_at(secret, step + 1)})
assert r.status_code == 401 and r.json()["detail"] == "Sign-in expired; enter your password again", r.text
c = password_step()
assert c.post("/api/login/2fa", json={"code": codes[0].lower()}).status_code == 200
assert password_step().post("/api/login/2fa", json={"code": codes[0]}).status_code == 401  # each code once
assert first.get("/api/account/security").json()["two_factor"]["recovery_codes_remaining"] == 9
assert mails("new@example.com")[-1].subject == "A recovery code was used"
r = first.post("/api/account/2fa/recovery-codes", json={"password": PASSWORD, "code": codes[1]})
new_codes = r.json()["recovery_codes"]
SECRETS_SEEN.extend(new_codes)
assert len(new_codes) == 10 and not set(new_codes) & set(codes)
assert password_step().post("/api/login/2fa", json={"code": codes[2]}).status_code == 401  # replaced
assert first.post("/api/account/2fa/disable", json={"password": "wrong-password-00", "code": new_codes[0]}).status_code == 400
assert first.post("/api/account/2fa/disable", json={"password": PASSWORD, "code": "000000"}).status_code == 400
assert first.post("/api/account/2fa/disable", json={"password": PASSWORD, "code": new_codes[0]}).json() == {"enabled": False}
assert browser().post("/api/login", json={"email": "new@example.com", "password": PASSWORD}).json()["two_factor_required"] is False
assert mails("new@example.com")[-1].subject == "Two-factor authentication turned off"
with Session() as db:
    stored = db.scalar(select(User).where(User.email == "new@example.com"))
    assert stored.totp_ciphertext is None and not db.scalars(select(RecoveryCode).where(RecoveryCode.user_id == stored.id)).all()

# An admin resets 2FA for a locked-out user; their sessions end.
setup = first.post("/api/account/2fa/setup", json={"password": PASSWORD}).json()
first.post("/api/account/2fa/enable", json={"code": totp.code_at(setup["secret"], totp.step_at())})
target = user("new@example.com").id
assert browser().post(f"/api/admin/users/{target}/reset-2fa").status_code == 401
assert client.post(f"/api/admin/users/{target}/reset-2fa").json()["two_factor"] is False
assert first.get("/api/dashboard").status_code == 401
first = signed_in("Browser A")
listed = client.get("/api/admin/users", params={"q": "new@"}).json()["items"][0]
assert listed["two_factor"] is False and listed["email_verified"] is True and listed["last_login_at"]

# --- export, closure request, locale, terms ---------------------------------------------------------------------------------
r = first.get("/api/account/export")
assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
exported = r.json()
assert exported["account"]["email"] == "new@example.com" and exported["workspaces"][0]["role"] == "owner"
assert "password_hash" not in r.text and "ciphertext" not in r.text
assert first.post("/api/account/closure-request", json={"reason": "Leaving"}).status_code == 201
with Session() as db:
    ticket = db.scalar(select(SupportTicket).where(SupportTicket.subject == "Account closure request"))
    assert ticket.category == "account"
assert first.put("/api/account/locale", json={"locale": "ja"}).json() == {"locale": "ja"}
assert user("new@example.com").locale == "ja"
assert client.post("/api/account/terms").json() == {"version": accounts.TERMS_VERSION}

# --- the audit log: every step, never a secret ----------------------------------------------------------------------------
with Session() as db:
    events = db.scalars(select(AuditEvent)).all()
actions = {event.action for event in events}
for expected in ("account.registered", "account.email_verified", "account.verification_sent",
                 "account.password_reset_requested", "account.password_reset", "account.password_changed",
                 "account.session_revoked", "account.sessions_revoked", "account.two_factor_enabled",
                 "account.two_factor_disabled", "account.recovery_code_used", "account.recovery_codes_regenerated",
                 "admin.user_two_factor_reset", "auth.login", "auth.two_factor", "account.export",
                 "account.closure_requested"):
    assert expected in actions, expected
assert any(e.action == "account.password_changed" and e.outcome == "failure" for e in events)
assert any(e.action == "auth.two_factor" and e.outcome == "failure" for e in events)
dumped = json.dumps([(e.action, e.details_json, e.target_id) for e in events])
for secret_value in [*SECRETS_SEEN, "first-password-11", "second-password-22", "third-password-33"]:
    assert secret_value not in dumped, secret_value
page = client.get("/api/admin/audit", params={"action": "account.", "limit": 5}).json()
assert page["total"] > 5 and len(page["items"]) == 5 and all(i["action"].startswith("account.") for i in page["items"])
failures = client.get("/api/admin/audit", params={"outcome": "failure"}).json()["items"]
assert failures and all(item["outcome"] == "failure" for item in failures)
assert first.get("/api/admin/audit").status_code == 403
mine = first.get("/api/account/activity").json()["items"]
assert mine and all(set(item) == {"at", "action", "outcome", "ip", "details"} for item in mine)

# --- durable rate limits: many sign-ins from one address -----------------------------------------------------------------------
ratelimit.LIMITS.update(DEFAULT_LIMITS)
with Session.begin() as db:
    db.execute(delete(RateLimitBucket))
statuses = [browser().post("/api/login", json={"email": f"x{n}@example.com", "password": "wrong-password-00"}).status_code
            for n in range(31)]
assert statuses[:30] == [401] * 30 and statuses[30] == 429, statuses
# CF-Connecting-IP from a peer that is not a trusted proxy is ignored: still the same address, still limited.
r = browser().post("/api/login", json={"email": "y@example.com", "password": "wrong-password-00"},
                   headers={"CF-Connecting-IP": "198.51.100.7"})
assert r.status_code == 429 and int(r.headers["retry-after"]) > 0, r.text
# Ten sign-ins per account per 15 minutes, whatever the address.
with Session.begin() as db:
    db.execute(delete(RateLimitBucket))
statuses = [browser().post("/api/login", json={"email": "new@example.com", "password": PASSWORD}).status_code
            for _ in range(11)]
assert statuses[:10] == [200] * 10 and statuses[10] == 429, statuses
print("ok")
'''


class AccountFlowTest(unittest.TestCase):
    def test_registration_verification_reset_sessions_two_factor_audit(self):
        result = run_program(ACCOUNTS)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])


OUTBOX = r'''
from app import mailer
from app.models import EmailOutbox, User
mailer.AUTO_DELIVER = False
with Session.begin() as db:
    owner = db.scalar(select(User))
    # Off: nothing is queued, and nothing fails.
    assert mailer.enqueue(db, to=owner.email, template="test", locale="vi", params={}) is None
    system_config.save(db, None, values={"email.enabled": True, "email.provider": "smtp",
                                         "email.from_email": "studio@example.com", "email.smtp.host": "127.0.0.1"},
                       section="email")
with Session.begin() as db:
    first = mailer.enqueue(db, to=owner.email, template="test", locale="vi", params={"time": "2026-10-02T00:00:00"},
                           dedupe="once")
    assert first and mailer.enqueue(db, to=owner.email, template="test", locale="vi", params={}, dedupe="once") is None
    row = db.get(EmailOutbox, first)
    assert row.payload_ciphertext and "2026-10-02" not in row.payload_ciphertext
# The provider fails: retried later with back-off, then given up; the parameters are erased.
calls = []
def broken(message):
    calls.append(message)
    raise mailer.EmailError("connection_failed")
mailer.SEND_OVERRIDE = broken
now = datetime.now(timezone.utc)
assert mailer.deliver_pending(now=now) == 0
with Session() as db:
    row = db.get(EmailOutbox, first)
    assert row.status == "queued" and row.attempts == 1 and row.last_error == "connection_failed"
    assert row.next_attempt_at.replace(tzinfo=timezone.utc) >= now + timedelta(seconds=59)
assert mailer.deliver_pending(now=now) == 0 and len(calls) == 1  # not due yet
later = now
for attempt in range(2, mailer.MAX_ATTEMPTS + 1):
    later = later + timedelta(hours=7)
    mailer.deliver_pending(now=later)
with Session() as db:
    row = db.get(EmailOutbox, first)
    assert row.status == "failed" and row.attempts == mailer.MAX_ATTEMPTS and row.payload_ciphertext is None
    assert len(calls) == mailer.MAX_ATTEMPTS
# A rejected address is not retried.
mailer.SEND_OVERRIDE = lambda message: (_ for _ in ()).throw(mailer.EmailError("rejected"))
with Session.begin() as db:
    second = mailer.enqueue(db, to="bad@example.com", template="test", locale="en", params={})
mailer.deliver_pending(now=later)
with Session() as db:
    assert db.get(EmailOutbox, second).status == "failed"
stats = None
with Session() as db:
    stats = mailer.stats(db)
assert stats["failed"] == 2 and stats["last_error"] in ("connection_failed", "rejected")
# Admin → Email → Send test email uses the saved settings, never shows them.
seen = []
mailer.SEND_OVERRIDE = lambda message: seen.append(message)
r = client.post("/api/admin/system-config/email/test", json={})
assert r.json()["ok"] is True and seen[0].to == "owner@example.com" and seen[0].subject.startswith("Email thử nghiệm")
mailer.SEND_OVERRIDE = lambda message: (_ for _ in ()).throw(mailer.EmailError("auth_failed"))
assert client.post("/api/admin/system-config/email/test", json={"to": "x@example.com"}).json()["error"] == "auth_failed"
assert client.post("/api/admin/system-config/email/test", json={"to": "nope"}).status_code == 422
with Session.begin() as db:
    system_config.save(db, None, secrets={"email.smtp.password": ("replace", "smtp-pass-SENTINEL")}, section="email")
config_view = client.get("/api/admin/system-config").json()
assert "smtp-pass-SENTINEL" not in json.dumps(config_view)
email_section = {item["key"]: item for item in config_view["sections"]["email"]}
assert email_section["email.smtp.password"]["configured"] is True and "value" not in email_section["email.smtp.password"]
print("ok")
'''


class OutboxTest(unittest.TestCase):
    def test_queue_retry_give_up_and_admin_test(self):
        result = run_program(OUTBOX)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])


MIGRATION = r'''
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
config = Config("alembic.ini")
engine = create_engine(json.load(open("instance/bootstrap.json"))["database_url"])
if engine.dialect.name != "sqlite":
    command.downgrade(config, "base")
command.upgrade(config, "0021_default_production_origin")
with engine.begin() as c:
    c.execute(text("INSERT INTO users (id, email, password_hash, is_admin, is_active) VALUES ('u1', 'a@b.c', 'x', true, true)"))
    c.execute(text("INSERT INTO login_sessions (token_hash, user_id, expires_at) VALUES ('h1', 'u1', '2030-01-01 00:00:00'), ('h2', 'u1', '2030-01-01 00:00:00')"))
command.upgrade(config, "head")
with engine.connect() as c:
    assert c.execute(text("SELECT email_verified_at FROM users WHERE id = 'u1'")).scalar() is not None
    ids = [row[0] for row in c.execute(text("SELECT id FROM login_sessions"))]
    assert len(ids) == 2 and all(ids) and len(set(ids)) == 2, ids
    tables = set(inspect(c).get_table_names())
    for name in ("account_tokens", "recovery_codes", "email_outbox", "audit_events", "rate_limit_buckets",
                 "workspace_invites", "backup_runs", "system_alerts"):
        assert name in tables, name
command.downgrade(config, "0021_default_production_origin")
with engine.connect() as c:
    assert c.execute(text("SELECT count(*) FROM login_sessions")).scalar() == 2
    assert "email_verified_at" not in {col["name"] for col in inspect(c).get_columns("users")}
    assert "account_tokens" not in set(inspect(c).get_table_names())
command.upgrade(config, "head")
print("ok")
'''


def run_migration(database_url: str, directory: str):
    target = Path(directory)
    for folder in ("app", "migrations"):
        shutil.copytree(ROOT / folder, target / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "alembic.ini", target / "alembic.ini")
    (target / "instance").mkdir()
    (target / "instance" / "bootstrap.json").write_text(json.dumps({"database_url": database_url}))
    return subprocess.run([sys.executable, "-c", MIGRATION], cwd=target, env={**os.environ, "PYTHONPATH": str(target)},
                          capture_output=True, text=True)


def postgresql_url() -> str:
    url = os.environ.get("REELFORGE_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql") or "test" not in url.rsplit("/", 1)[-1].lower():
        return ""
    return "postgresql+psycopg://" + url[len("postgresql://"):] if url.startswith("postgresql://") else url


SUPPORT_REPLY_EMAIL = r'''
import time
from app import mailer
SENT = []
mailer.SEND_OVERRIDE = lambda message: SENT.append(message)
with Session.begin() as db:
    system_config.save(db, None, values={"email.enabled": True, "email.provider": "smtp",
                                         "email.from_email": "studio@example.com", "email.smtp.host": "127.0.0.1"},
                       section="email")
asker = TestClient(app, headers={"Origin": "http://testserver"})
assert asker.post("/api/register", json={"email": "asker@example.com", "password": "asker-password-1",
                                         "workspace_name": "Asker", "accept_terms": True}).status_code == 201
ticket = asker.post("/api/support/tickets", json={"subject": "Slow export", "category": "bug",
                                                  "description": "It takes long."}).json()
reply = client.post(f"/api/admin/support/{ticket['id']}/messages", json={"body": "Looking into it."})
assert reply.status_code == 201, reply.text
# Delivered by the request's own kick, without waiting for the scheduler.
deadline = time.monotonic() + 10
def replied():
    return any(m.to == "asker@example.com" and "Slow export" in m.subject for m in SENT)
while time.monotonic() < deadline and not replied():
    time.sleep(0.05)
assert replied(), [m.subject for m in SENT]
print("ok")
'''

# Whatever queues email in a request must start the delivery right after its transaction commits.
QUEUEING = {"queue_email", "send_verification", "enqueue", "email_owners", "settle", "apply_paid", "payment_rejected",
            "payment_settled"}


class DeliveryKickTest(unittest.TestCase):
    def test_support_reply_email_leaves_at_once(self):
        result = run_program(SUPPORT_REPLY_EMAIL)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-6000:])
        self.assertTrue(result.stdout.strip().endswith("ok"), result.stdout[-2000:])

    def test_every_function_that_queues_email_kicks_delivery(self):
        import ast
        tree = ast.parse((ROOT / "app" / "main.py").read_text(encoding="utf-8"))
        missing = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name in QUEUEING:
                continue
            calls = {call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", None)
                     for call in ast.walk(node) if isinstance(call, ast.Call)}
            if calls & QUEUEING and "kick" not in calls:
                missing.append(node.name)
        self.assertEqual(missing, [])


class MigrationTest(unittest.TestCase):
    def test_sqlite_upgrade_marks_accounts_verified_and_keeps_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_migration(f"sqlite:///{Path(directory)}/instance/test.db", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_postgresql_upgrade_marks_accounts_verified_and_keeps_sessions(self):
        url = postgresql_url()
        if not url:
            self.skipTest("set REELFORGE_TEST_DATABASE_URL to an isolated PostgreSQL test database")
        with tempfile.TemporaryDirectory() as directory:
            result = run_migration(url, directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])

    def test_revision_ids_fit_postgresql(self):
        for name in ("0022_account_security", "0023_workspace_team", "0024_operations"):
            self.assertLessEqual(len(name), 32)
            self.assertTrue((ROOT / "migrations" / "versions" / f"{name}.py").is_file())


if __name__ == "__main__":
    unittest.main()
