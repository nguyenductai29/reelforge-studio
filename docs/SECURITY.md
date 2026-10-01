# Security and observability (v1.0)

How accounts, sessions and requests are protected, where the trust boundaries are, and what an operator can watch.
Teams and roles: [TEAMS.md](TEAMS.md). Email: [EMAIL.md](EMAIL.md). Backups: [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md).

## Accounts

| Topic | Behaviour |
| --- | --- |
| Passwords | scrypt (n = 2^14, r = 8, p = 1, 16-byte salt). At least 12 characters, different from the email. A sign-in for an unknown or deactivated account spends the same scrypt work as a wrong password, so response times reveal nothing (`app/passwords.py`). |
| First administrator | Only while no account exists, and only from the server itself: the API refuses first-run setup (403) from any public internet address, through Cloudflare or directly. Create it with `npm run create-admin` on the server, or through an SSH tunnel to `127.0.0.1:3001`. `/health/ready` reports `setup_open` and `deploy.sh` prints a reminder until it exists. |
| Registration | Accepting the Terms and the Privacy Policy is required; their version and the time are stored (`users.terms_version`, `terms_accepted_at`). Accounts created before can accept later (a banner asks). |
| Email verification | A new account can sign in at once; "not verified" is shown until the emailed link is opened. Links are valid 24 h and work once; resending is limited to 3 per hour. Accounts that existed before migration 0022 count as verified. Inviting members needs a verified address while email works. |
| Forgot password | `/forgot-password` gives the same answer whether or not the account exists. The emailed link is valid 60 minutes and works once. Setting a new password signs out **every** session and sends a "password changed" email. |
| Change password | Current password, new password twice. Other sessions are signed out, this one stays; a notification email follows. |
| Two-factor (TOTP) | Optional, RFC 6238 (SHA-1, 6 digits, 30 s, one step of drift, a code is never accepted twice). Enrolling needs the password, then one correct code from the app (QR code or the key by hand). Ten recovery codes are shown once, stored hashed, and each works once. Turning 2FA off or regenerating codes needs the password and a code. System administrators are asked to turn it on (a banner and a readiness check); an administrator can reset a user's 2FA (audited, emailed). |
| Lockout | Five wrong passwords for one email from one address block that pair for 15 minutes and email the account owner; the per-address and per-account rate limits below apply as well. |
| Data and closure | Settings → Security → *Download account data* (JSON: profile, studios, sessions, orders, support requests, security log; no media, no secret). *Request account closure* opens a support request: an administrator handles it, because payment records may have to be kept. Nothing is deleted automatically, and the product makes no claim of GDPR compliance. |

Every token sent by email (verification, password reset, invitation) is 256 random bits (`secrets.token_urlsafe(32)`),
stored only as its SHA-256 digest, has an expiry and works once. Links carry it in the URL **fragment**
(`/reset-password#token=…`), which browsers never send to a server, a proxy or a `Referer` header.

## Sessions

* The cookie `rf_session` is `HttpOnly`, `SameSite=Strict` and `Secure` (production default, Admin → System settings →
  General), valid 7 days. The database keeps only the token's SHA-256 digest.
* Settings → Security lists each session with its device (user agent), address, sign-in time, last activity (updated at
  most every 5 minutes) and expiry, never a token. *Sign out* ends one; *Sign out all other sessions* ends the rest.
* With 2FA on, the password step sets a separate cookie, `rf_challenge` (path `/api/login`, 5 minutes, 5 tries); only the
  second step creates the session.

## Requests

**Cross-site request protection.** Besides `SameSite=Strict`, every state-changing request to `/api/` (POST, PUT, PATCH,
DELETE) must carry an `Origin` (or else a `Referer`) equal to the public origin (or the API's own); a request carrying
the session cookie must carry one of them at all. Provider callbacks under `/api/webhooks/` are exempt and are
authenticated by their signatures instead (payOS HMAC, OnePAY secure hash). The OnePAY return URL is a browser
navigation (GET) that never settles a payment by itself.

**Security headers.** The API sets `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
`X-Frame-Options: DENY`, `Permissions-Policy`, `Cross-Origin-Opener-Policy: same-origin`, a locked-down CSP on `/api/`
(`default-src 'none'; frame-ancestors 'none'; sandbox`), and HSTS when the public origin is HTTPS. The pages
(`frontend/next.config.ts`) get a CSP limited to the site itself (`'unsafe-inline'` scripts only because Next.js inlines
its bootstrap), `frame-ancestors 'none'`, `object-src 'none'`, `form-action 'self'`, HSTS in production, and the same
other headers. OAuth and payment redirects are top-level navigations, which these policies do not restrict.

**Errors.** Every error is JSON `{"detail", "code", "request_id"}` with a stable `code` (`rate_limited`,
`validation_error`, `internal_error`, …). An unexpected error answers `Internal server error` with the request ID,
never a stack trace or a secret; the log line with the same `request_id` has the details. Every response carries
`X-Request-ID`. The interface shows a sentence (and the request ID for server errors), never a raw response.

## The client address and its trust boundary

```
browser ──HTTPS──▶ Cloudflare edge ──tunnel──▶ cloudflared (this server) ──▶ Next.js 127.0.0.1:3001 ──▶ API 127.0.0.1:8000
                   sets CF-Connecting-IP                                    forwards the header
```

* Cloudflare overwrites any `CF-Connecting-IP` a client sends; Next.js passes it on to the API.
* The API believes the header **only** when the connection comes from a trusted proxy: by default the loopback networks
  (`127.0.0.0/8`, `::1/128`). Otherwise the client address is the TCP peer.
* `X-Forwarded-For` is never used.
* Both settings are in Admin → System settings → Security (trusted proxy networks, the header name; an empty header
  name trusts no header).
* The guarantee holds because the API and Next.js listen on loopback only: anything else that can open a connection to
  `127.0.0.1:8000` or `127.0.0.1:3001` on this server could choose the header. Keep it that way.

The address is used for rate limits, sessions, the audit log and lockout emails.

## Rate limits

Durable, in PostgreSQL (`rate_limit_buckets`): fixed windows counted with one atomic `INSERT … ON CONFLICT DO UPDATE`,
shared by every API process and worker; subjects are stored as SHA-256 digests. Over a limit the answer is 429 with
`Retry-After` and the code `rate_limited`.

| Scope | Limit |
| --- | --- |
| Sign-in per address / per account | 30 / 10 per 15 min |
| First-run setup, registration (per address) | 10 per hour each |
| Forgot password per address / per account | 10 / 3 per hour |
| Password reset, email verification (per address) | 20, 30 per hour |
| Resend verification (per account) | 3 per hour |
| 2FA codes per address / per account | 30 / 10 per 15 min |
| Account changes (password, 2FA setup) | 10 per 15 min |
| Support requests / messages (per user) | 10 / 60 per hour |
| Checkout per user / per studio | 20 / 10 per hour |
| Invitations per studio / invitation lookups per address | 30 per day / 60 per hour |
| Test email, account export (per admin / user) | 10 per hour each |

## Audit log

`audit_events`: time, action, outcome (`success`, `failure`, `denied`), actor, studio, target, client address and
request ID, plus a few details. Details whose key looks like a secret (password, token, secret, code, key, cookie,
authorization, recovery, credential…) are dropped before storing. Recorded: sign-in, sign-out, lockouts, 2FA, every
password and email change, sessions revoked, invitations, role changes, removals, ownership transfers, studio
creation, admin changes of users, plans, payments, gateways and system settings, master key backup confirmations,
rejected payment callbacks, refused first-run setups, checkouts created, terms accepted, social channels connected or
disconnected, and the server-side recovery command. **Admin → Audit log** filters by action, outcome and user email on the server, a page at a
time (`GET /api/admin/audit` also takes `workspace_id`, `since` and `until`). Each user sees their own recent security
activity in Settings → Security.

## Secrets at rest

One master key (`/etc/reelforge/master.key`) outside the database; each kind of secret uses a key derived from it
(HKDF, one purpose each): admin settings, payment gateways, TOTP secrets (per user), the email outbox's parameters
(erased once sent). OAuth tokens and upload sessions use the master key's Fernet directly. Secret inputs in the admin
UI are write-only: a saved value is never sent back. See [SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md#the-master-key).

## Break-glass recovery

For what the web interface cannot solve (the only administrator forgot their password while email does not work, or
lost both their authenticator and their recovery codes), the operator runs, **on the server**, as the service account:

```bash
.venv/bin/python -m app.account_recovery reset-password --email admin@example.com   # prompts twice, never echoes
.venv/bin/python -m app.account_recovery reset-2fa --email admin@example.com
```

Both sign the account out everywhere, are recorded in the audit log (`via: server_command`) and email the account
owner when email works. They need shell access to the server, the database URL and the master key: nothing reachable
from the internet. Another system administrator can also reset a user's 2FA in Admin → Users.

## Observability

| Endpoint | What | Access |
| --- | --- | --- |
| `GET /health/live` | The process answers | Loopback (the API listens there only) |
| `GET /health/ready` | Database reachable, migrations at head, master key usable (a valid key file, and a sample of the stored secrets decrypts with it). No paid provider is called. 200 or 503 with the failing check; `warnings: ["setup_open"]` while no account exists. `deploy.sh` waits for it. | Loopback |
| `GET /internal/metrics` | Prometheus text: HTTP requests, errors and durations by method and route template; jobs, runs, publications, payments waiting or failed, email outbox, media disk, backup age, active alerts, sign-in failures, worker heartbeats | A scraper on the server, or a signed-in system administrator |

Metric labels never contain an email, a title, a project text or an ID, and stay bounded whatever a client sends
(an unknown HTTP method is counted as `OTHER`, an unknown path as `unmatched`): routes appear as templates
(`/api/projects/{project_id}`).

**Alerts** (`app/alerts.py`, evaluated by the scheduler worker every 5 minutes): a worker that stopped reporting or
reports errors, the media disk at 80 % / 90 %, no successful backup within `backups.max_age_hours` or a failed backup,
many failed jobs in the last hour, rejected payment callbacks, a missing or wrong master key, email failing. Each
condition notifies the system administrators in the app when it starts, then at most once every 12 hours while it
lasts, and resolves silently. Admin → Verification shows the active ones.

**Logs** are JSON lines in the journal with `request_id`, never a secret ([BACKUP_RECOVERY.md](BACKUP_RECOVERY.md#logs)
for the size limits). Sentry is not used; the structured logs, the metrics and the alerts replace it.
