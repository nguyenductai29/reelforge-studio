# ReelForge Studio v1.0 — release checklist

Every gate below must pass on the production server, on the commit being released, before `v1.0.0` is tagged. This
file is the index of the gates. **It holds no status:** each gate is recorded in exactly one place, named in its
*Recorded as* column.

| Recorded as | Where the result lives |
| --- | --- |
| `preflight` | `bash deploy/release-preflight.sh` on the server: PASS, WARN, FAIL or MANUAL per check, every time it runs (exit status 1 on any FAIL). Read-only; never prints a secret |
| `ci` | The automated suites. They count only through CI on the deployed commit: gate `release_ci_green` |
| a key such as `email_dns` | **Admin → Verification**, recorded by the person who checked it: *Passed*, *Failed*, *Not applicable* or *Not checked*, who, when and a note (an order code, a video ID, a file name; never a secret). Nothing is recorded automatically |

* *Not applicable* is allowed only for a provider the installation does not use: payOS, OnePAY, Runway, TikTok,
  Facebook. Every other gate must pass.
* `bash deploy/release-report.sh` combines the pre-flight, the recorded gates and the CI result GitHub reports for the
  commit, and gives the verdict. The release stays a **release candidate** until it says `READY_FOR_TAG`.
* How to perform each manual gate: [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md). The state at release time and the tag
  procedure: [V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md).

Related: [PRODUCTION_BOOTSTRAP.md](PRODUCTION_BOOTSTRAP.md) · [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md) ·
[SECURITY.md](SECURITY.md) · [EMAIL.md](EMAIL.md) · [TEAMS.md](TEAMS.md) · [LOAD_BASELINE.md](LOAD_BASELINE.md) ·
the release audit: [V1_RELEASE_AUDIT.md](V1_RELEASE_AUDIT.md)

## Automated evidence (development machine, 2026-10-02, release closure)

Results of the automated suites on the release candidate, on a development machine. **They do not replace CI**: only
a green CI run on the deployed commit passes `release_ci_green`.

| Check | Command | Result |
| --- | --- | --- |
| Backend tests, SQLite | `python -m unittest discover -s tests` | Passed: 600 tests, 20 skipped (13 need PostgreSQL, 2 need live providers, 1 needs FFmpeg, 4 need symlinks, which this Windows machine lacks) |
| Backend tests, PostgreSQL 16 (locks, SKIP LOCKED, ON CONFLICT, settlement, ledger, isolation, concurrent quota and checkout, job claims, migrations both ways, backup → restore rehearsal) | the same with `REELFORGE_TEST_DATABASE_URL` and `REELFORGE_TEST_PG_BIN` | Passed: 600 tests, 7 skipped (live providers, FFmpeg, symlinks) |
| Alembic: upgrade head, `alembic check`, downgrade base, upgrade head, `alembic check` (PostgreSQL 16) | CI job *migrations* | Passed on PostgreSQL 16.2: no drift, head `0025_verification_status` |
| Frontend typecheck and production build | `npm run typecheck`, `npm run build` | Passed |
| Browser tests (11 tests: first-run setup refused from a public address then done locally, sign-in, forgot/reset, sessions and a session lost in an open tab, 2FA, invitations and switching, project and workflow, manual VietQR confirmed by an admin, support and notifications, admin pages, release gates recorded in Admin → Verification, non-admin refusal) | `e2e/` on PostgreSQL 16.2 | Passed: 11 / 11 |
| Load baseline | `tests/load_check.py` | Recorded in LOAD_BASELINE.md and re-run after Phase 27; no errors, no double claim, no double credit |
| Responsive and accessibility pass: 33 pages at 390×844, 768×1024, 1366×768, 1680×1050; axe-core (WCAG 2.1 A/AA rules) at 1366×768; keyboard: skip link, dialog focus trap, Escape, named controls | QA scripts on the E2E stack | No horizontal scrolling of the page; no axe violation; keyboard checks passed. Automated rules catch only part of accessibility: this is not a compliance claim (run on the Phase 26 build; later interface changes are covered by the browser tests) |

## Release

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| R1 | CI is green on the exact deployed commit: backend on SQLite (Python 3.11 and 3.14) and PostgreSQL, migrations both ways, frontend (Node 20 and 22), Playwright | The GitHub Actions run of that commit (the report reads it); a local run does not count | `release_ci_green` |
| R2 | `./deploy.sh` on the release commit ends with "ReelForge deployment completed OK", every service active | Its output | `release_deploy` |
| R3 | The pre-flight has no FAIL on the release commit | `bash deploy/release-preflight.sh --expect-commit <commit>` | `release_preflight` |
| R4 | The report says `READY_FOR_TAG` | `bash deploy/release-report.sh` | `preflight` |

## Bootstrap

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| B1 | `instance/bootstrap.json` holds only `database_url`, chmod 600; no `frontend_origin` / `secure_cookies` | Pre-flight, SYSTEM CONFIG | `preflight` |
| B2 | Public origin `https://reelforge.mul-service.com`, Secure cookies on (Admin → System settings → General) | Pre-flight, SYSTEM CONFIG (`--expect-origin`) | `preflight` |
| B3 | The API listens on `127.0.0.1:8000` and Next.js on `127.0.0.1:3001` only | Pre-flight, PORTS (`ss -ltn`) | `preflight` |
| B4 | Cloudflare Tunnel `studio.imokome-cloud.com` → `http://127.0.0.1:3001`; the site opens over HTTPS | A browser, then `curl -sI` | `security_headers` |
| B5 | The first administrator was created on the server (`npm run create-admin`); setup is closed; an active system administrator exists | Pre-flight, SECURITY | `preflight` |

## Database

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| D1 | `alembic current` is the head, `0025_verification_status` | Pre-flight, DATABASE | `preflight`, `migration_upgraded` |
| D2 | `/health/ready` answers `"status": "ok"` | Pre-flight, HEALTH | `preflight` |
| D3 | The application's database role is not a superuser | Pre-flight, DATABASE | `preflight` |
| D4 | Existing accounts still sign in after the upgrade | Sign in with an account from before the release | `migration_upgraded` |

## Master key

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| K1 | `/etc/reelforge/master.key`, mode 600, owned by the service account; every stored secret decrypts with it | Pre-flight, MASTER KEY | `preflight` |
| K2 | A copy is stored off the server (password manager or encrypted USB), **not** next to the dumps | By hand | `master_key_file`, `backup_offsite` |
| K3 | The off-server backup is confirmed in Admin → System settings → Backups (fingerprint matches) | Pre-flight, MASTER KEY | `preflight` |
| K4 | The off-server copy decrypts every secret in the recovery rehearsal | BK6 | `restore_rehearsal` |

## Storage

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| S1 | Media root `/srv/data/videos/reelforge` on the HDD: exists, writable by the service account, free space under the 80 % / 90 % alert levels | Pre-flight, STORAGE; Admin → Operations shows the disk | `preflight`, `storage_on_hdd` |
| S2 | `reelforge-media-maintenance.timer` enabled; a dry run lists what it would delete and deletes nothing | Pre-flight, SERVICES; `python -m app.media_maintenance --intermediates` | `preflight`, `maintenance_timer`, `cleanup_dry_run` |
| S3 | A media copy exists elsewhere and `python -m app.media_manifest verify` reports nothing missing | [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md#media) | `media_backup_verified` |

## Backups

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| BK1 | `reelforge-backup.timer` enabled; a manual `systemctl start reelforge-backup` succeeded | Pre-flight, BACKUPS | `preflight`, `backup_timer` |
| BK2 | `/srv/data/backups/reelforge` mode 700, dumps mode 600, no key file beside them | Pre-flight, BACKUPS | `preflight` |
| BK3 | The last success is under 26 h old and the newest dump is on disk | Pre-flight, BACKUPS | `preflight` |
| BK4 | Retention reviewed (14 daily / 8 weekly / 6 monthly) | Admin → System settings → Backups | `backup_timer` |
| BK5 | Dumps are copied off the server on a schedule (encrypted), apart from the key | By hand | `backup_offsite` |
| BK6 | Rehearsal: `deploy/restore-check.sh --dump <newest>`, then into `reelforge_restore_test` with the off-server key copy: counts plausible, migration version, every secret decrypts; scratch database dropped and the key copy shredded afterwards | [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md#backup-and-recovery) | `restore_rehearsal` |

## Security

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| SE1 | Every system administrator has 2FA on and the recovery codes stored; sign-in asks for the code; a recovery code works once | Pre-flight, SECURITY (warns about an administrator without 2FA), then by hand | `security_two_factor` |
| SE2 | *Sign out all other sessions* ends a second browser's session | By hand | `security_sessions` |
| SE3 | Wrong passwords end in "too many attempts" (429) and the lockout email arrives; an account that does not exist gets the same answer as a wrong password | By hand, through the public site | `security_rate_limit` |
| SE4 | The audit log shows your real public address for a sign-in through the tunnel (not `127.0.0.1`), and no password, token or key | Admin → Audit log | `security_client_ip` |
| SE5 | A forged `CF-Connecting-IP` / `X-Forwarded-For` sent from outside does not change the recorded address | `curl` through the public site | `security_spoofed_headers` |
| SE6 | HTTPS with HSTS, CSP with `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`; `rf_session` is `HttpOnly`, `Secure`, `SameSite=Strict` | `curl -sI`, browser devtools | `security_headers` |
| SE7 | A POST with a foreign `Origin` is refused (403) | `curl` | `security_foreign_origin` |
| SE8 | `systemctl --failed` lists no ReelForge unit; every enabled worker is active with a fresh heartbeat | Pre-flight, SERVICES | `preflight` |
| SE9 | The break-glass command runs (`python -m app.account_recovery --help`; never reset a real account to test it) | Pre-flight, SECURITY | `preflight` |
| SE10 | The trusted proxies do not trust every address; the client address header is `CF-Connecting-IP` | Pre-flight, SYSTEM CONFIG | `preflight` |

## Email

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| E1 | Provider configured (SMTP or Resend); *Send test email* arrives | Admin → System settings → Email | `email_test_sent` |
| E2 | SPF, DKIM and DMARC pass | The received message's headers | `email_dns` |
| E3 | A new account receives the verification email and the link verifies it | By hand | `email_verification` |
| E4 | Forgot password: the email arrives, the link sets a new password, other sessions end | By hand | `email_password_reset` |
| E5 | "Password changed" arrives after the reset | By hand | `email_password_changed` |
| E6 | An invitation email arrives and the link joins the studio | By hand | `email_invitation` |
| E7 | A support reply email arrives | By hand | `email_support_reply` |

## AI providers (paid: in this order, once each)

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| A1 | Gemini text: a script step | [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md#ai-providers) | `gemini_text_live` |
| A2 | Gemini TTS: a voice step | `python -m app.smoke_test voice --live` or a Voice step | `gemini_tts_live` |
| A3 | Transcription: a short clip with speech | A Transcript step | `transcription_live` |
| A4 | Runway image (if Runway is used) | An Image step | `runway_image_live` |
| A5 | Runway video (if Runway is used) | A Video step, one short clip | `runway_video_live` |
| A6 | Each successful step creates its asset and is charged once; a provider failure is refunded | The studio's credit history | `ai_credits_once` |

## Render

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| RE1 | `ffmpeg` and `ffprobe` found; the subtitle font installed | Pre-flight, FFMPEG; `python -m app.render_worker --check` | `preflight`, `ffmpeg_verified` |
| RE2 | Voice, subtitles and music rendered into one MP4 under the media root | A workflow ending in Render | `final_render_live` |
| RE3 | Slideshow: Article → Video and Product Video | The templates | `article_video_live`, `product_video_live` |
| RE4 | Movie Recap | The template on a short source you may use | `movie_recap_live` |
| RE5 | Movie Review | The template on a short source you may use | `movie_review_live` |

## Payments (manual VietQR first, then payOS, then OnePAY)

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| P1 | Manual VietQR: a real small transfer; *I have transferred* → awaiting confirmation; the admin confirms; plan and credits once; receipt; confirming again changes nothing | [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md#payments) | `bank_qr_round_trip` |
| P2 | payOS: saved and tested, webhook registered and received, one small payment paid once, credits once | Same | `payos_config_saved`, `payos_webhook_received`, `payos_payment`, `payos_credits_once` |
| P3 | OnePAY sandbox: saved, checked, a test card succeeds, a cancel shows cancelled, IPN received, QueryDR confirms | Same | `onepay_sandbox_configured`, `onepay_sandbox_check`, `onepay_sandbox_payment`, `onepay_sandbox_cancel`, `onepay_ipn_received`, `onepay_querydr_verified` |
| P4 | OnePAY production: saved after confirmation, one small real payment, credits once | Same | `onepay_production_configured`, `onepay_production_payment`, `onepay_credits_once` |
| P5 | Wording "VietQR / Bank Transfer" and "Credit / Debit Card" in every language | `tests/test_product_audit.py`, the billing browser test | `ci` |

## Publishing

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| PU1 | YouTube upload, private; note the video ID | [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md#publishing) | `youtube_upload` |
| PU2 | TikTok, inbox draft; note the publish ID (if TikTok is used) | Same | `tiktok_upload` |
| PU3 | Facebook Reel; note its URL (if Facebook is used) | Same | `facebook_reel` |
| PU4 | A scheduled publication goes out at its time | Same | `scheduled_publishing` |

## Operations

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| O1 | Live notifications arrive through Cloudflare without reloading (Server-Sent Events) | Admin → Verification: *Check notification stream* from outside | `notification_realtime` |
| O2 | A system alert reaches the admins | Stop one worker past its stale time, then start it | `alerts_delivered` |
| O3 | A user's support request reaches Admin → Support; the reply reaches the user (app, notification, email); an account closure request arrives the same way and is handled as in [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md#support-and-account-closure) | By hand | `support_round_trip` |
| O4 | *Download account data* returns the JSON; *Request account closure* opens a support request | `tests/test_phase22.py` | `ci` |
| O5 | `/internal/metrics` answers on `127.0.0.1`; journald limits installed | Pre-flight, HEALTH and SERVICES | `preflight` |
| O6 | Admin → Verification shows no unexpected red readiness item; active alerts understood | The report lists the readiness checks needing attention | `preflight` |
| O7 | Server reboot: every service, timer and the tunnel come back by themselves; `/health/ready` is ok; a workflow runs | [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md#reboot) | `server_reboot` |

Optional, not a gate: re-run `tests/load_check.py` on the server against a disposable database
([LOAD_BASELINE.md](LOAD_BASELINE.md)).

## Legal

| # | Gate | How | Recorded as |
| --- | --- | --- | --- |
| L1 | Terms of Service: every `[bracketed]` item filled in and reviewed by a lawyer for the jurisdiction; `TERMS_VERSION` (`app/accounts.py`) set to the published version (changing it asks every user to accept again) | [V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md#legal) lists the placeholders | `legal_terms_reviewed` |
| L2 | Privacy Policy: processors, retention, contact and applicable law filled in, reviewed | Same | `legal_privacy_reviewed` |
| L3 | /terms and /privacy are public in every language; registration requires accepting them | `tests/test_phase22.py`, `tests/test_phase26.py` | `ci` |
