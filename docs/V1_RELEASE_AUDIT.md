# ReelForge Studio v1.0 — release audit (Phase 27)

> **Historical record: the Phase 27 audit of 2026-10-02, made on `17a8bf5`.** Its commit, test counts and migration
> head are those of that day. The current state of the release (candidate commit, head `0027_movie_sources`,
> CI, gates) is [V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md); the current automated evidence is in
> [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md#automated-evidence-development-machine-2026-10-03-release-closure).

Status values: **PASS** (verified here, automatically or by reading the code), **FAIL** (a blocker that remains),
**MANUAL** (needs the real server, a real provider, a real payment or a person), **NOT_APPLICABLE**.
Nothing marked MANUAL was verified here, and nothing in [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md) was ticked.

## A. Release commit

| Item | Value |
| --- | --- |
| Branch | `feat/studio-foundation` |
| Audited from | `17a8bf5` ("commit phase 26") |
| Release candidate | `17a8bf5` + the Phase 27 changes listed below (uncommitted when this was written; the operator commits) |

## B. Migration head

| Check | Status |
| --- | --- |
| Head is `0024_operations`; no migration added in Phase 27; 0001–0024 unchanged (the release closure later added `0025_verification_status`, and the domain change `0026_change_production_origin`: [V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md)) | PASS |
| PostgreSQL 16: `upgrade head` → `alembic check` → `downgrade base` → `upgrade head` → `alembic check`, no drift | PASS |
| Every migration test both ways on SQLite and PostgreSQL 16, rows kept | PASS |
| 0022–0024 only add nullable columns and new tables: code that predates them keeps running on the new schema | PASS (read) |

## C. Automated test results (development machine, 2026-10-02)

| Suite | Command | Result |
| --- | --- | --- |
| Backend, SQLite | `python -m unittest discover -s tests -v` | PASS: 576 tests, 19 skipped (12 need PostgreSQL, 2 live providers, 1 FFmpeg, 4 symlinks this Windows machine cannot create) |
| Backend, PostgreSQL 16 | the same with `REELFORGE_TEST_DATABASE_URL` and `REELFORGE_TEST_PG_BIN` | PASS: 576 tests, 7 skipped (2 live providers, 1 FFmpeg, 4 symlinks) |
| Alembic on PostgreSQL 16 | upgrade, check, downgrade, upgrade, check | PASS |
| Frontend | `npx tsc --noEmit`, `npm run build` | PASS |
| Browser tests (Playwright, PostgreSQL stack) | `python e2e/prepare.py` then `npx playwright test` | PASS: 10 / 10 (now also the public first-run refusal and a session lost in an open tab) |
| Load baseline | `python tests/load_check.py` | PASS: re-run after the Phase 27 changes, within a few percent of [LOAD_BASELINE.md](LOAD_BASELINE.md); no error, no double claim, no double credit |
| Python 3.12 syntax of every module; dependencies' minimum Python ≤ 3.11 | compile check, package metadata | PASS (3.11 itself runs in CI) |
| CI workflow | YAML parsed; action versions exist (`checkout`, `setup-python`, `setup-node`, `upload-artifact` v7) | PASS; the GitHub run itself is MANUAL |

## D. Security audit findings

| # | Severity | Finding | Fix | Status |
| --- | --- | --- | --- | --- |
| 1 | BLOCKER | The documented bootstrap opened the Cloudflare route **before** creating the first administrator in the browser; anyone reaching the site first could claim the system administrator | `/api/setup` accepts first-run setup only from the server itself (403 from any public address, through Cloudflare or directly); `/api/status.setup_here` and a sign-in notice; `/health/ready` warns `setup_open`; `deploy.sh` reminds; the bootstrap docs create the administrator with `npm run create-admin` before the tunnel | PASS |
| 2 | HIGH | `deploy.sh` restarted the workers but never checked them, and tried the frontend once: a crashing service still ended in "completed OK" | Waits up to 30 s for the frontend, checks every restarted unit a few seconds later, exits 1 naming the failed ones | PASS (script); MANUAL on the server |
| 3 | HIGH | No way back in for the only administrator after a forgotten password without email, or a lost authenticator and recovery codes | `python -m app.account_recovery reset-password / reset-2fa` (server only; revokes every session; audited; emailed) | PASS |
| 4 | HIGH (docs) | `STORAGE.md` advised keeping the encryption key **with** the database dumps, and several documents still named `REELFORGE_TOKEN_ENCRYPTION_KEY` as the key | Every document now says: the master key file, backed up off the server and never next to the dumps | PASS |
| 5 | MEDIUM | Sign-in for an unknown or deactivated email skipped scrypt: response times revealed which accounts exist | The same scrypt work in every case (`app/passwords.py`) | PASS |
| 6 | MEDIUM | Two concurrent sign-ins could both accept one TOTP code (no row lock while checking the last used step) | The user row is locked while a code is checked | PASS |
| 7 | MEDIUM | `/health/ready` claimed to verify the master key against the stored secrets but only checked the key file | It also decrypts a sample of the stored secrets (shared with the alert) | PASS |
| 8 | MEDIUM | Any HTTP method became a metrics label: a client could grow the metrics without bound | Unknown methods are labelled `OTHER` | PASS |
| 9 | MEDIUM | `billing.checkout_created` was declared but never recorded; terms acceptance and channel connections were not audited | Recorded | PASS |
| 10 | MEDIUM | An exception while queueing an email (encryption, insert) would roll back the change that caused it, a settled payment included | Queueing problems are logged and the email skipped | PASS |
| 11 | MEDIUM | The OnePAY checkout sent Next.js's loopback address as the buyer's address | The address resolved behind the trusted proxy | PASS |
| 12 | MEDIUM | A relative or system backup directory in the settings would be used (and made chmod 700) | Refused before anything is touched | PASS |
| 13 | MEDIUM | Retrying or publishing another studio's publication or run answered 409 (uniform, so no existence leak, but unlike every other ID) | 404 | PASS |
| 14 | MEDIUM | A session revoked elsewhere left the open tab failing request by request | Any 401 re-checks the session and returns the tab to sign-in | PASS |
| 15 | MEDIUM | CI tested Python 3.13 (production runs 3.14, 3.11 is the documented minimum) and Node 20 (end-of-life) only | Python 3.11 + 3.14, Node 20 + 22, `upload-artifact@v7`, pip caches | PASS (file); MANUAL (first GitHub run) |
| 16 | LOW | Expired sessions and used tokens are never purged (ignored everywhere) | Documented | NOT_APPLICABLE |
| 17 | LOW | Registration answers "Email already exists" (inherent; rate limited per address) | Documented | NOT_APPLICABLE |
| 18 | LOW | Forgot-password does a few more milliseconds of database work for an existing account (same answer) | Documented | NOT_APPLICABLE |
| 19 | LOW | The session cookie has no `__Host-` prefix: a hostile sibling subdomain could plant cookies (the operator controls the domain) | Documented | NOT_APPLICABLE |
| 20 | LOW | Any system administrator can reset another administrator's 2FA (system administrators are fully trusted) | Documented | NOT_APPLICABLE |

**Audited without a finding (PASS):** token entropy, hashing, expiry and single use (verification, reset, invitation,
2FA challenge); recovery codes single use under a row lock; password reset revokes every session, password change and 2FA
enrolment every other one; logout; lockout per email and address; session cookie `HttpOnly`, `SameSite=Strict`, `Secure`
in production; no endpoint creates a session around the second factor (registration and setup create accounts without
2FA; the invitation page uses the normal sign-in); every one of the 164 routes inventoried — all state-changing routes
call the same-origin check besides the middleware, webhooks are exempt and signed; client address only from trusted
proxies (spoofed `CF-Connecting-IP`, `X-Forwarded-For`, `X-Real-IP`, `Forwarded`, `True-Client-IP` tested); errors
without stack traces; metrics labels (route templates, states, channels, providers, worker kinds; no email, studio ID,
title or user text); alerts deduplicated with a 12-hour cooldown; `/internal/metrics` not proxied by Next.js.

## E. Permission and workspace isolation

| Check | Status |
| --- | --- |
| Every route that takes an ID looks the record up within the active studio (or the user) — inventory of all ID routes | PASS |
| 33 endpoints called with another studio's IDs answer 403/404 and change nothing (projects, workflows, runs, steps, assets, publications, AI tools, support tickets, orders, invitations, memberships, notifications, sessions, switching) | PASS (`test_phase27`) |
| Lists show nothing of another studio (dashboard, publications, tickets, orders, members) | PASS |
| Switching studios: the IDs of the previous studio stop working at once | PASS |
| Viewer: 15 mutations refused; editor: channels, members, settings, default models, billing, cleanup, transfer refused; "editors may publish" off refuses publishing; admin: checkout and transfer refused, owner untouchable; owner-only checkout | PASS |
| A removed member is refused on the next request | PASS |
| Browser: invitations, switching, viewer refusal, removal, no project leaking between studios | PASS (`e2e/02-team`) |

## F. Payments

| Check | Status |
| --- | --- |
| One settlement path; exact amount; the order's own provider; credits and subscription extension once under row locks | PASS |
| Duplicate and late callbacks idempotent; a payment for an expired, failed or cancelled order still settles (the money arrived) | PASS |
| `pending` → `awaiting_confirmation` → `paid` / `rejected`; admin-only manual confirmation of the exact amount, recorded and audited; no "mark paid" for payOS or OnePAY | PASS |
| Disabled provider: no new checkout, existing orders still settle | PASS |
| Owner-only checkout; history for owners and admins | PASS |
| Receipts and failure emails deduplicated; an email problem never undoes a settlement | PASS |
| Load: 51 orders × 3 signed payOS callbacks, 51 paid, 51 credit grants | PASS ([LOAD_BASELINE.md](LOAD_BASELINE.md)) |
| Real manual transfer, real payOS payment, OnePAY sandbox, one small OnePAY production payment | MANUAL |

## G. Backup and recovery

| Check | Status |
| --- | --- |
| `.partial` written then checked with `pg_restore --list`, renamed only when valid; removed on failure | PASS |
| `pg_dump` failure recorded, nothing pruned; newest dump never pruned; GFS retention | PASS |
| Password only through `PGPASSWORD`; never printed | PASS |
| Backup directory: relative or system path refused | PASS (fixed) |
| Restore check: refuses the production database and any non-disposable name; counts; decrypts with a key copy; wrong key detected (PostgreSQL rehearsal test) | PASS |
| Master key never copied next to the dumps; confirmation by fingerprint; readiness and alerts when it changes or cannot decrypt | PASS |
| Backup status, alert and readiness agree (same `max_age_hours`) | PASS |
| `deploy.sh` warns when the backup timer is not enabled and when units differ or are missing | PASS |
| Timer on the server, off-server copies, a rehearsal on production data | MANUAL |

## H. Deployment

| Check | Status |
| --- | --- |
| Units: `User`/`Group`, `WorkingDirectory`, `ExecStart`, `Restart=always` for services, optional `EnvironmentFile=-`, `UMask=0077` | PASS (read) |
| Hardening keeps `/home/tai/apps` and `/srv/data` writable and `/etc/reelforge/master.key` readable (`ProtectSystem=full`, no `ProtectHome`, no `strict`); workers use scratch folders under the media root, so `PrivateTmp` is safe | PASS (read) |
| `deploy.sh`: pull, dependencies, development-override refusal, master-key safety, migrations, frontend build, restarts, readiness, frontend, every service checked, failure exit status, setup and backup reminders | PASS (script, `bash -n`, tests) |
| `/health/live` liveness only; `/health/ready` database, migration head, master key (file and decryption); no paid provider, gateway or social platform | PASS |
| Running the units and `deploy.sh` on the real server; a reboot | MANUAL |

## I. Documents corrected

`FINAL_PRODUCT_AUDIT.md` (rewritten for v1.0: head `0024_operations`, security, teams, observability, backups, legal,
limitations), `IMPLEMENTATION_STATUS.md` (resolved risks and missing items marked; Phase 27 section), `README.md`,
`PRODUCTION_BOOTSTRAP.md` (administrator before the tunnel; deploy checks), `home-server-deployment.md` (setup only on
the server; master key wording), `SECURITY.md` (first administrator, timing, break-glass recovery, readiness, metrics),
`EMAIL.md` (no admin password reset exists), `BACKUP_RECOVERY.md` (directory rule, lock-out), `STORAGE.md` (key never
next to the dumps; units; runtime file optional), `MULTI_PLATFORM_PUBLISHING.md` and `SOCIAL_VIDEO_WORKFLOW.md` (master
key; channels added since Phase 9), `OPERATIONS.md` (the manual payment path), `RELEASE_V1_CHECKLIST.md` (B6, SE11,
SE12 added; evidence updated). Migration heads, ports (127.0.0.1:8000, 127.0.0.1:3001), the domain
(`studio.imokome-cloud.com` at the time; `reelforge.mul-service.com` since migration 0026) and unit names agree across
them.

## J. Remaining manual checks

Every item of [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md) (all "Not checked") and the 41 items of Admin →
Verification. (The release closure since turned the checklist into an index of gates recorded in Admin →
Verification or by `deploy/release-preflight.sh`: 62 then, 68 since the domain change; the current state is in
[V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md).)
In short:

| Area | MANUAL checks |
| --- | --- |
| Server | `deploy.sh` OK on the release commit, units installed and active, journald limits, a reboot |
| First administrator | Created on the server before the tunnel; 2FA on; recovery codes stored |
| Email | A real provider; test email; SPF/DKIM; verification, reset, invitation and support reply emails received |
| AI | Gemini text and TTS, Runway image and video, transcription; credits charged once and refunded on failure |
| Render | Voice, subtitles, music, slideshow, movie recap / review |
| Publishing | YouTube, TikTok, Facebook, a scheduled publication |
| Payments | Manual VietQR with a real transfer, payOS, OnePAY sandbox, one small OnePAY production payment |
| Security | Real client address through Cloudflare in the audit log; HSTS/CSP on the public site; rate limit and lockout email; session revocation |
| Operations | Backup timer, off-server copies of dumps and of the master key, restore rehearsal with the key copy, media copy and manifest |
| Legal | Terms and Privacy filled in and reviewed by a lawyer; `TERMS_VERSION` |
| CI | The first GitHub Actions run of this workflow is green |

## K. Known limitations

See [FINAL_PRODUCT_AUDIT.md § 16](FINAL_PRODUCT_AUDIT.md#16-known-limitations): password reset needs email (or the
server command); no studio deletion or SSO; account closure handled by an administrator; expired sessions not purged;
alerts in-app only; one API process by default; no key rotation; manual VietQR needs an administrator; legal texts are
templates.

## L. Release blockers

| Blocker | Status |
| --- | --- |
| Code or configuration blocker found in this audit | None remaining (finding 1 fixed and tested) |
| Legal review of `/terms` and `/privacy` | MANUAL — release gate |
| Live verification on the real server (email, AI, payments, publishing, Cloudflare, backups, reboot) | MANUAL — release gate |

**Verdict:** a code-complete v1.0 **release candidate**. It becomes production-approved only after the MANUAL gates
above pass on the real server; until then it must not be called production-ready.
