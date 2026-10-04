# Final product audit (v1.0: Phases 14–27)

> Snapshot: branch `feat/studio-foundation`, v1.0 release candidate, 2026-10-03 (code of `56ada10`), updated
> 2026-10-04 for the movie source phase added after the release closure ([MOVIE_SOURCES.md](MOVIE_SOURCES.md)).
> Database head: `0027_movie_sources`. What v1.0 ships, in short: [RELEASE_NOTES_V1.md](RELEASE_NOTES_V1.md).
> Release state and the remaining production gates: [V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md).
> The release audit itself (findings, fixes, test results, what remains manual) is [V1_RELEASE_AUDIT.md](V1_RELEASE_AUDIT.md);
> the operator's gate is [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md).

**Production readiness**

- **Bootstrap:** a fresh installation runs with only the database URL (`instance/bootstrap.json`) and
  `/etc/reelforge/master.key`; no `.env.runtime`. The first administrator is created on the server itself
  (`npm run create-admin`); first-run setup is refused from public addresses.
- **Configuration:** everything else in Admin → System settings and Admin → Payments, stored in PostgreSQL with secrets
  encrypted, applied without restarts. Every environment variable the backend still reads is classified and tested.
- **Services:** hardened systemd units (`deploy/systemd/`) need no secret; `deploy.sh` stops without a usable master key,
  checks the migration head, every restarted service and `/health/ready`, and fails when one is not right.
  `deploy/release-preflight.sh` checks the server read-only; `deploy/release-report.sh` gives the release verdict.
- **Security, teams, email, observability, backups:** see §§ 2–6.

**Rule applied since Phase 16:** every control in the production interface works. An unfinished feature was built or
removed; no "coming soon" badge, banner, disabled placeholder switch or preview-only template remains.
`tests/test_product_audit.py` fails on a `SoonBadge` or `ComingSoonBanner`, a "coming soon" string in any language, a
hard-coded disabled switch, a step-library entry or preset the backend cannot run, a template without a backend graph,
or Instagram in the channel lists.

## 1. Implemented features

**Content creation**

- **Templates** (`/create`), each a workflow the backend builds and runs: Social video; YouTube Short and YouTube
  (16:9); TikTok video; Facebook Reel; Movie Recap; Movie Review; Movie Review and Movie Recap from a movie source;
  Repurpose existing content; Article → Video; Product Video; Blank.
- **Step library:** 54 entries, all executable (some are presets of an existing step: short and long scripts, movie
  review, ending explained, the movie review / recap / ending scripts, thumbnail, key moments, text to video, merge
  clips, preview, schedule post, upload image).
- **Movie sources** (after the release closure, migration 0027): temporary movies kept in the operator's Google Drive
  (the studio's server import folder, direct https URL, Drive inbox), checked with ffprobe, retained 7 days by default (extendable,
  capped), deleted automatically after a successful review or at expiry, never while a run uses them; a Movie Review /
  Recap / Ending Explained pipeline with grounded visual analysis, a timeline, time-ranged sections and short excerpts.
  [MOVIE_SOURCES.md](MOVIE_SOURCES.md)
- **Background Music** under the narration (1–100 %, loop or play once); **image slideshows**; workspace content
  defaults (platform, tone, length).

**Home (the member's dashboard):** an idea box that creates a project; the studio's figures (credits, projects, runs
and publications in the last 30 days, storage); what needs the member's attention (runs to review, failed or blocked
runs, failed publications, channels to reconnect, low credits, storage, an inactive plan, support replies, held
credits, runs in progress: only what the member's role can act on); quick templates, the onboarding
checklist, recent workflows, projects and runs, AI usage by kind and publishing per channel. One request
(`GET /api/home`), within the member's role and the active studio; only recorded data, no estimate or trend.

**Library, media and storage:** scripts in the Library; add uploads to a project; Media page filters and owner-confirmed
deletion; storage used / quota with warnings at 70, 80, 90 and 100 %; "Delete intermediate media" after a preview.

**Accounts and security (Phase 22):** email verification, forgot/reset password (generic answer, 60-minute single-use
link, every session revoked), change password, optional TOTP 2FA with ten single-use recovery codes, session list with
per-session and "all other" sign-out, account data export, account closure request, Terms/Privacy acceptance with
version and time. [SECURITY.md](SECURITY.md)

**Transactional email (Phase 22):** SMTP or Resend; 15 templates in Vietnamese, English and Japanese (HTML and text);
an outbox written in the same transaction, delivered at once and retried with back-off. [EMAIL.md](EMAIL.md)

**Teams (Phase 23):** owner / admin / editor / viewer, enforced by the API; email invitations (7 days, single use);
studio switcher; own-studio creation; ownership transfer with re-authentication; immediate removal. [TEAMS.md](TEAMS.md)

**Billing:** VietQR / Bank Transfer (manual bank QR confirmed by an administrator, or payOS) and Credit / Debit Card
(OnePAY). Only configured methods appear; owners buy, owners and admins see the history; the manual transfer shows its
"awaiting confirmation" state until an administrator confirms it.

**Notifications and support:** a live notification bell (Server-Sent Events with polling fallback) for runs,
publishing, payments, credits, storage and support; in-app support requests with replies.

**Legal (Phase 26):** `/terms` and `/privacy` templates in every language, linked from every public page and the
registration form. They are templates with `[bracketed]` operator items and a visible notice; legal review is a
release gate.

**Administration:** the console fits the window, each table scrolls inside it. Eleven tabs:

| Tab | What it shows and does |
| --- | --- |
| Overview | The first tab (`GET /api/admin/overview`, system administrators only, counted in the database): users, active users, studios, active subscriptions, paid volume, jobs in 24 h and the system state; what needs attention; system health, AI usage, payments, credits, growth, publishing, storage, support and recent activity, each opening the tab that handles it |
| Users | Search, role and status filters; create account; view, lock, unlock; reset a user's 2FA; sign a user out everywhere |
| Studios & credits | Search, plan and status filters, storage; change plan, adjust credits, pause, activate |
| Plans | Name, price, limits, monthly credits, storage limit, and why a plan is not purchasable |
| Payments | Search, provider and status filters; confirm or reject manual VietQR transfers (exact amount); refresh with the provider; gateway configuration (write-only secrets, enable/disable, OnePAY Sandbox/Production/Advanced) |
| Support | Filters; reply, change status or priority, resolve, close |
| Credit reconciliation | The review of held credits |
| Operations | Worker heartbeats, jobs (the `movie` queue included), stuck-work audit, media disk and studios per storage level, the movie source Drive (files, bytes, oldest source, expiring within 24 hours, failing deletions; from the table, never Drive's quota) |
| Verification | Readiness (database, migrations, storage, FFmpeg, workers, AI keys, publishing, payments, realtime, support, security, configuration, backups, email, accounts, alerts, movie sources), a stream check, and the 75 release gates (seven of them, optional, for movie sources), each passed, failed, not applicable or not checked, recorded by an admin with the date and a note |
| System settings | Security (master key, trusted proxies), General, Email, AI providers, Social OAuth, Storage, Backups, Runtime, Credit pricing, Notifications, Movie sources (Google Drive with *Test Drive connection*, retention, folders, frames; every studio's sources); each value's source |
| Audit log | Security and administration events, filtered and paginated on the server |

## 2. Security architecture (Phases 22, 24, 27)

| Area | Behaviour |
| --- | --- |
| Passwords | scrypt; 12 characters minimum, not the email. An unknown or deactivated account costs the same work at sign-in (no timing oracle) |
| Sessions | `rf_session`: HttpOnly, SameSite=Strict, Secure in production, 7 days; only its SHA-256 is stored; listed and revocable without tokens |
| Second factor | TOTP with replay protection under a row lock; recovery codes hashed and single use; a separate 5-minute challenge cookie with 5 tries |
| Emailed tokens | 256 random bits, stored hashed, expiring, single use, carried in the URL fragment |
| First administrator | Only while no account exists, and only from the server itself (`npm run create-admin` or an SSH tunnel); a public address gets 403; `/health/ready` and `deploy.sh` warn while setup is open |
| Client address | `CF-Connecting-IP` believed only from trusted proxies (loopback by default); `X-Forwarded-For` never used |
| Cross-site requests | `SameSite=Strict` plus an Origin/Referer check on every state-changing `/api/` request; provider webhooks exempt (signed) |
| Rate limits | Durable PostgreSQL counters for sign-in, setup, registration, password reset, verification, 2FA, account changes, support, checkout, invitations, test email and export |
| Headers | API: strict CSP, nosniff, Referrer-Policy, X-Frame-Options DENY, Permissions-Policy, COOP, HSTS on HTTPS; pages: their own CSP with `frame-ancestors 'none'` |
| Errors | `{detail, code, request_id}`, no stack trace, `X-Request-ID` on every response |
| Audit | Sign-ins, account and 2FA changes, sessions, team changes, checkouts, channel connections, terms acceptance, admin changes, payment confirmations and rejected callbacks; secret-looking detail keys dropped |
| Recovery | `python -m app.account_recovery` (server only) resets a password or turns 2FA off when no administrator can, audited and emailed |

Details: [SECURITY.md](SECURITY.md).

## 3. Teams and isolation (Phase 23)

| Permission | Owner | Admin | Editor | Viewer |
| --- | :---: | :---: | :---: | :---: |
| See the studio | ✓ | ✓ | ✓ | ✓ |
| Create and edit content, run workflows | ✓ | ✓ | ✓ | |
| Publish | ✓ | ✓ | ✓ (unless turned off) | |
| Channels, default models, studio settings, members | ✓ | ✓ | | |
| Payment history | ✓ | ✓ | | |
| Buy a plan, transfer ownership | ✓ | | | |

Every request resolves the active studio (session, then the account's last studio, then its oldest membership) and
checks the membership and permission on the server. Records of another studio answer 404 through every endpoint that
takes their ID (`tests/test_phase27.py` walks them all). Studio deletion is not part of v1.0.

## 4. Billing providers

| Provider | Method | Credentials | Callback URLs |
| --- | --- | --- | --- |
| Manual VietQR (bank QR) | VietQR / Bank Transfer | Bank BIN, account number and holder, transfer prefix | None: an administrator confirms the exact amount in Admin → Payments |
| payOS | VietQR / Bank Transfer | Client ID, API Key, Checksum Key (Admin → Payments, encrypted) | Webhook `https://<origin>/api/webhooks/payos` |
| OnePAY | Credit / Debit Card | Merchant ID, Access Code, Hash Key, QueryDR user and password; Sandbox / Production / Advanced | IPN `https://<origin>/api/webhooks/onepay`; return `https://<origin>/api/billing/onepay/return` |

- One settlement path (`payments.apply_paid`): the provider must be the order's, the amount exact; the subscription is
  extended and the credits posted once, under row locks. Duplicate and late callbacks are idempotent; a payment for an
  order that already expired still settles, because the money arrived.
- A browser return never pays. The only manual path is the manual VietQR confirmation: system admins only, exact
  amount, recorded as an order event and in the audit log.
- Credentials are write-only and encrypted with the master key (HKDF per purpose); a saved configuration that cannot
  be decrypted is an error, never a silent fallback. Disabling a gateway never strands a pending order.
- Receipts and failure emails are deduplicated, and an email problem never undoes a settlement.

Details: [PAYMENTS.md](PAYMENTS.md).

## 5. Observability (Phase 24)

`/health/live`; `/health/ready` (database, migrations at head, master key usable and decrypting a sample of the stored
secrets; never a paid provider; a `setup_open` warning while no account exists); `/internal/metrics` (Prometheus, for a
scraper on the server or a system admin; bounded labels: methods, route templates, states, channels, providers, worker
kinds); alerts for stale workers, disk 80 / 90 %, overdue or failed backups, failed jobs, rejected payment callbacks,
the master key and failing email, with a 12-hour cooldown. JSON logs with request IDs.

## 6. Backups and recovery (Phase 25)

A daily `pg_dump` timer (checked archive, chmod 600, 14 daily / 8 weekly / 6 monthly, newest never removed, a
relative or system directory refused); restore checks into a scratch database with a copy of the master key; media
manifests with checksums; the master key backed up separately and confirmed by fingerprint. [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md)

## 7. Hidden or deferred features

| Feature | Decision | Why |
| --- | --- | --- |
| Instagram publishing | Hidden | Needs an Instagram professional account, its own publish flow and Meta review |
| YouTube URL input | Hidden | Downloading YouTube media conflicts with YouTube's terms |
| Research, Scene planner, Storyboard, Stock media, Image → video, Voice clone, Sound effects, Audio mixer, timeline editing steps | Hidden | No backend, licensing or consent work in scope |
| `/ai/*` single-tool pages | Redirect to `/create` | The same features are templates |
| "Auto-schedule at best time", recap/thumbnail/provider-ID toggles | Removed | No behaviour |
| Studio deletion | Not in v1.0 | No safe way yet to settle its subscription, credits, media and publications |
| Automatic deletion of uploads | Not offered | Never without an explicit user action |
| Crypto payments | Not offered | Out of scope |

## 8. Social publishing and scheduling

| Channel | How | Approval |
| --- | --- | --- |
| YouTube | Google OAuth `youtube.upload`, resumable upload | Unverified Google projects upload private videos only |
| TikTok | Login Kit + Content Posting API (`video.upload`), inbox drafts | App approved for `video.upload` |
| Facebook | Facebook Login, Page Reels | Meta app review for accounts outside the app's roles |

One approved video can go to several channels, each with its own metadata and job. Tokens are encrypted with the
master key. Uncertain uploads become `needs_attention`, never a false success. A publication can be scheduled (UTC),
moved or cancelled until its upload starts; the scheduler worker queues it on time. [MULTI_PLATFORM_PUBLISHING.md](MULTI_PLATFORM_PUBLISHING.md),
[SCHEDULING.md](SCHEDULING.md)

## 9. Storage, quotas and retention

One media root (Admin → System settings → Storage); files at `<root>/<workspace_id>/<asset_id>`. Plan storage limits
(Trial 1 GB, Standard 10 GB, Pro 30 GB by default) are enforced before anything is stored, under the studio's row lock.
Final renders and uploaded sources are kept; intermediates expire 30 days after their run has a final render and no
publication uses them; scratch folders 3 days; `.part` files 1 day. The daily cleanup (`reelforge-media-maintenance.timer`,
03:00) deletes only verified files inside the root. [STORAGE.md](STORAGE.md)

## 10. Required processes

| Process | Needed for |
| --- | --- |
| API (`uvicorn app.main:app`, 127.0.0.1:8000) and Next.js (127.0.0.1:3001) | Always |
| `reelforge-worker@text`, `@image`, `@video`, `@voice`, `@render`, `@source` | Writing, images, AI video, narration, render and clip extraction, sources and transcription |
| `reelforge-worker@youtube`, `@social` | YouTube, TikTok and Facebook uploads |
| `reelforge-worker@scheduler` | Scheduled publications, email retries, system alerts, movie source retention |
| `reelforge-worker@movie` | Only with movie sources enabled: imports to Google Drive, deletions, Prepare Movie, Visual Analysis, movie excerpts |
| `reelforge-media-maintenance.timer` | Daily media cleanup at 03:00 |
| `reelforge-backup.timer` | Daily database backup at 02:30 |

## 11. Bootstrap and configuration

Production needs only `instance/bootstrap.json` (the database URL) and `/etc/reelforge/master.key` (chmod 600,
`python -m app.master_key init`). Admin → System settings and Admin → Payments hold the rest. The legacy environment
variables (`/etc/reelforge/runtime.env`, optional) remain a fallback for settings nobody saved; a saved value always
wins. `REELFORGE_TOKEN_ENCRYPTION_KEY` is only the legacy source of the master key, copied into the key file by
`python -m app.master_key init`. [SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md), [PRODUCTION_BOOTSTRAP.md](PRODUCTION_BOOTSTRAP.md)

## 12. Database migration head

`0027_movie_sources`. Migrations 0001–0026 are unchanged; 0022–0025 and 0027 only add nullable columns to existing
tables and new tables, so code that predates them keeps working on the new schema, and 0026 changes data only (the
production domain).

| Migration | Adds | Test |
| --- | --- | --- |
| `0015_admin_payments_profiles` | `user_profiles`; indexes for the admin and payment pages | `test_admin_payments_migration.py` |
| `0016_storage_lifecycle` | Plan storage limits; asset kinds and expiry columns | `test_storage_migration.py` |
| `0017_notify_support_verify` | `notifications`, `support_tickets`, `support_messages`, `verification_checks` | `test_phase18_migration.py` |
| `0018_admin_payment_config` | `payment_provider_configs`, `payment_config_audit` | `test_phase19_migration.py` |
| `0019_system_configuration` | `system_config`, `system_config_audit`, `payment_orders.transfer_reported_at`, `payment_order_events` | `test_phase20_migration.py` |
| `0020_manual_payment_statuses` | Data only: `awaiting_confirmation` and `rejected` manual orders | `test_phase21.py` |
| `0021_default_production_origin` | Data only: the production origin with Secure cookies, replacing only the old defaults | `test_production_origin.py` |
| `0022_account_security` | User verification, 2FA and terms columns; session ids, devices and activity; `account_tokens`, `recovery_codes`, `email_outbox`, `audit_events`, `rate_limit_buckets`; existing accounts marked verified | `test_phase22.py` |
| `0023_workspace_team` | `workspace_invites`; the active studio per session and per user; membership dates | `test_phase23.py` |
| `0024_operations` | `backup_runs`, `system_alerts` | `test_phase25.py` |
| `0025_verification_status` | `verification_checks.status` (passed, failed, not applicable; NULL is not checked); verified rows become passed | `test_phase28.py` |
| `0026_change_production_origin` | Data only: exactly `https://studio.imokome-cloud.com` becomes `https://reelforge.mul-service.com` (Secure cookies on), with OAuth redirect overrides that were exactly its callbacks; any other origin stays | `test_domain_migration.py` |
| `0027_movie_sources` | `movie_sources`, `movie_source_uses`; `assets.movie_source_id` (nullable); indexes by workspace, status and next attempt, expiry, Drive file, project, run | `test_movie_migration.py` |

Every migration test runs on SQLite and, with `REELFORGE_TEST_DATABASE_URL`, on PostgreSQL 16, in both directions,
keeping every row. CI also runs `alembic check` after upgrading and after a full downgrade and upgrade.

## 13. FFmpeg requirements

System `ffmpeg` and `ffprobe` plus Noto fonts (`sudo apt install -y ffmpeg fonts-noto-core fonts-noto-cjk`); check with
`python -m app.render_worker --check`. Needed by the API, the render worker, the source worker and (with movie sources)
the movie worker (`python -m app.movie_worker --check`).

## 14. Recommended home-server directories

| Disk | Holds |
| --- | --- |
| SSD | OS, application, virtualenv, PostgreSQL, `/etc/reelforge` |
| HDD at `/srv/data` | `videos/reelforge` (media root) and `backups/reelforge` (daily dumps, chmod 700) |

A backup on the same HDD does not survive that disk failing: copy the dumps off the server, and keep the master key
separately from them.

## 15. Verification

**Automated (no paid or live service):** backend tests on SQLite and PostgreSQL 16, Alembic upgrade/check/downgrade,
frontend typecheck and build, the browser suite on PostgreSQL (31 tests in 12 specs, including the layout at seven
window sizes from 390 to 1920 px and movie sources against an in-memory Google Drive), a real FFmpeg run of the movie
pipeline on a synthetic movie, a load baseline ([LOAD_BASELINE.md](LOAD_BASELINE.md)), an accessibility pass.
Current results: [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md#automated-evidence-development-machine-2026-10-04-movie-source-phase)
(the release closure's: [2026-10-03](RELEASE_V1_CHECKLIST.md#automated-evidence-development-machine-2026-10-03-release-closure));
CI on the release candidate: [V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md).

**Manual, on the real server:** every gate of [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md), recorded in
Admin → Verification or by the pre-flight ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)): real email, real AI providers, real
payments (manual VietQR, payOS, OnePAY sandbox and one small production card payment), real YouTube / TikTok / Facebook
uploads and scheduling, the client address through Cloudflare, backups and a restore rehearsal, a server reboot, and the
legal review. Operator-verified before v1.0: Gemini text, Runway `gen4.5` and `gen4_image`.

## 16. Known limitations

**Accounts and teams**

- Password reset needs working email; otherwise only the server command (`python -m app.account_recovery`) or another
  administrator (2FA reset) helps.
- No single sign-on, no studio deletion, no per-studio audit view (the audit log is for system admins).
- Account closure is a support request handled by an administrator; nothing is deleted automatically.
- Registration answers "Email already exists" for a taken address (rate limited); sign-in and password reset do not
  reveal accounts.
- Expired sessions and used tokens stay in their tables (ignored everywhere); a periodic cleanup is not part of v1.0.

**Payments**

- Manual VietQR depends on an administrator checking the bank account; no bank statement integration.
- No key rotation: changing the master key makes saved credentials unreadable (re-enter them).
- One configuration per provider (no separate sandbox and production profiles); refunds outside the app; renewal is
  manual.

**Operations**

- Alerts are in-app notifications to system admins only (no email or push to an operator).
- One API process by default; the load baseline shows ample room for a studio ([LOAD_BASELINE.md](LOAD_BASELINE.md)).
- Rate limits are fixed in code.
- Each open notification stream polls the database every few seconds: fine for a home server, a pub/sub channel would
  be needed for thousands of concurrent users.

**Content**

- One background track per render, no ducking or fades; stills have no motion; Product Video uses AI images.
- One media root and one retention period for all intermediates; a run whose intermediates expired cannot be
  re-rendered.
- TikTok uploads are inbox drafts; YouTube uploads from an unverified Google project stay private.

**Legal**

- `/terms` and `/privacy` are templates: an operator must fill them in and have them reviewed before launch.

## Remaining SoonBadge / ComingSoonBanner

**Count: 0.** Both components were deleted; `tests/test_product_audit.py` fails if one comes back.
