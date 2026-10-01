# ReelForge Studio v1.0 — release checklist

Work through every item on the production server before calling the release done. Each item is **Not checked**,
**Passed**, **Failed** or **Not applicable**; write the date, who checked and a note (an order reference, a file name,
a screenshot). **Nothing in the manual sections is ticked automatically, and nobody should tick an item they did not
check themselves.** Admin → Verification has the same live items as an in-app checklist.

Related: [PRODUCTION_BOOTSTRAP.md](PRODUCTION_BOOTSTRAP.md) · [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md) ·
[SECURITY.md](SECURITY.md) · [EMAIL.md](EMAIL.md) · [TEAMS.md](TEAMS.md) · [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md) ·
[LOAD_BASELINE.md](LOAD_BASELINE.md)

## Automated evidence (development machine, 2026-10-02)

Results of the automated suites on the release candidate. They do not replace the manual checks below; CI must show
the same on the commit that is deployed (see *Live verification*).

| Check | Command | Result |
| --- | --- | --- |
| Backend tests, SQLite | `python -m unittest discover -s tests` | Passed: 566 tests, 19 skipped (12 need PostgreSQL, 2 need live providers, 1 needs FFmpeg, 4 need symlinks, which this Windows machine lacks) |
| Backend tests, PostgreSQL 16 (locks, SKIP LOCKED, ON CONFLICT, settlement, ledger, isolation, concurrent quota and checkout, job claims, migrations both ways, backup → restore rehearsal) | the same with `REELFORGE_TEST_DATABASE_URL` and `REELFORGE_TEST_PG_BIN` | Passed: 566 tests, 7 skipped (live providers, FFmpeg, symlinks) |
| Alembic: upgrade head, `alembic check`, downgrade base, upgrade head, `alembic check` (PostgreSQL 16) | CI job *migrations* | Passed: no drift |
| Frontend typecheck and production build | `npm run typecheck`, `npm run build` | Passed |
| Browser tests (10 flows: sign-in, forgot/reset, sessions, 2FA, invitations and switching, project and workflow, manual VietQR confirmed by an admin, support and notifications, admin pages, non-admin refusal) | `e2e/` on SQLite and on PostgreSQL | Passed: 10 / 10 on each |
| Load baseline | `tests/load_check.py` | Recorded in LOAD_BASELINE.md; no errors, no double claim, no double credit |
| Responsive and accessibility pass: 33 pages at 390×844, 768×1024, 1366×768, 1680×1050; axe-core (WCAG 2.1 A/AA rules) at 1366×768; keyboard: skip link, dialog focus trap, Escape, named controls | QA scripts on the E2E stack | No horizontal scrolling of the page; no axe violation; keyboard checks passed. Automated rules catch only part of accessibility: this is not a compliance claim |

## Bootstrap

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| B1 | `instance/bootstrap.json` holds only `database_url` (chmod 600); no `frontend_origin` / `secure_cookies` | Not checked | | |
| B2 | Admin → System settings → General: `https://studio.imokome-cloud.com`, Secure cookies on | Not checked | | |
| B3 | `ss -ltnp`: the API listens on `127.0.0.1:8000` and Next.js on `127.0.0.1:3001` only | Not checked | | |
| B4 | Cloudflare Tunnel: `studio.imokome-cloud.com` → `http://127.0.0.1:3001`; the site opens over HTTPS | Not checked | | |
| B5 | `./deploy.sh` finished with "ReelForge deployment completed OK" on the release commit | Not checked | | |

## Database

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| D1 | `python -m alembic current` shows `0024_operations` (head); 0022–0024 applied without error | Not checked | | |
| D2 | `curl -fsS http://127.0.0.1:8000/health/ready` answers `"status": "ok"` | Not checked | | |
| D3 | The application's database role is not a superuser and owns only its database | Not checked | | |
| D4 | Existing accounts can still sign in after the upgrade (marked verified by 0022) | Not checked | | |

## Master key

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| K1 | `python -m app.master_key status`: the key file `/etc/reelforge/master.key`, mode 600, owned by the service account | Not checked | | |
| K2 | A copy is stored off the server (password manager or encrypted USB), **not** next to the dumps | Not checked | | |
| K3 | Admin → System settings → Backups: key backup confirmed (fingerprint matches; Admin → Verification is green) | Not checked | | |
| K4 | The copy decrypted every secret in the recovery rehearsal (R2) | Not checked | | |

## Storage

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| S1 | Media root `/srv/data/videos/reelforge`, *Check* passes (writable, free space shown) | Not checked | | |
| S2 | `reelforge-media-maintenance.timer` enabled; a manual run succeeded | Not checked | | |
| S3 | Admin → Operations shows the media disk and the 80 % / 90 % levels | Not checked | | |
| S4 | A media copy exists elsewhere and `python -m app.media_manifest verify` reports nothing missing | Not checked | | |

## Backups

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| BK1 | `reelforge-backup.timer` enabled (`systemctl list-timers`), a manual `systemctl start reelforge-backup` succeeded | Not checked | | |
| BK2 | `/srv/data/backups/reelforge` is mode 700, dumps mode 600; no `master.key` in it | Not checked | | |
| BK3 | Admin → Verification shows the last success and an age under 26 h | Not checked | | |
| BK4 | Retention reviewed (14 daily / 8 weekly / 6 monthly) | Not checked | | |
| BK5 | Dumps are copied off the server on a schedule (encrypted) | Not checked | | |

## Security

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| SE1 | Every system administrator has 2FA on and their recovery codes stored | Not checked | | |
| SE2 | 2FA live: sign-in asks for the code; a recovery code works once | Not checked | | |
| SE3 | Sessions: *Sign out all other sessions* ends a second browser's session | Not checked | | |
| SE4 | Rate limits: repeated wrong passwords end in "too many attempts"; the lockout email arrives | Not checked | | |
| SE5 | Cloudflare client address: the audit log shows your real public address for a sign-in through the tunnel, not `127.0.0.1` | Not checked | | |
| SE6 | `curl -sI https://studio.imokome-cloud.com` shows HSTS, CSP with `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy` | Not checked | | |
| SE7 | A POST with a foreign `Origin` is refused (403) | Not checked | | |
| SE8 | Browser devtools: `rf_session` is `HttpOnly`, `Secure`, `SameSite=Strict` | Not checked | | |
| SE9 | Admin → Audit log lists the sign-ins and changes above, with no password, token or key | Not checked | | |
| SE10 | After installing the hardened units: `systemctl --failed` is empty and every worker is active | Not checked | | |

## Email

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| E1 | Admin → System settings → Email: provider configured, *Send test email* received | Not checked | | |
| E2 | SPF and DKIM pass (the received message's headers) | Not checked | | |
| E3 | A new account receives the verification email and the link verifies it | Not checked | | |
| E4 | Forgot password: the email arrives, the link sets a new password, other sessions end, "password changed" arrives | Not checked | | |
| E5 | An invitation email arrives and the link joins the studio | Not checked | | |
| E6 | A support reply email arrives | Not checked | | |

## AI providers

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| A1 | Gemini text (a script step) | Not checked | | |
| A2 | Gemini TTS (a voice step) | Not checked | | |
| A3 | Runway image | Not checked | | |
| A4 | Runway video | Not checked | | |
| A5 | Transcription (a source with speech) | Not checked | | |
| A6 | Credits charged once per successful step; refunded on a provider failure | Not checked | | |

## Payments

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| P1 | Manual VietQR: a real small transfer, *I have transferred*, the admin confirms, the plan activates once, the receipt arrives | Not checked | | |
| P2 | payOS: webhook URL registered; a real payment settles automatically | Not checked | | |
| P3 | OnePAY sandbox: a test card payment settles (IPN) | Not checked | | |
| P4 | OnePAY production: one small real transaction settles | Not checked | | |
| P5 | Wording reads "VietQR / Bank Transfer" and "Credit / Debit Card" in every language | Not checked | | |

## Publishing

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| PU1 | YouTube upload (private first) | Not checked | | |
| PU2 | TikTok | Not checked | | |
| PU3 | Facebook | Not checked | | |
| PU4 | A scheduled publication goes out at its time | Not checked | | |

## Workers

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| W1 | Admin → Operations: every worker reports (text, image, video, voice, render, source, youtube, social, scheduler) | Not checked | | |
| W2 | Render: voice, subtitles, music, slideshow | Not checked | | |
| W3 | Movie recap / review render | Not checked | | |
| W4 | Server reboot: every service and timer comes back by itself; `/health/ready` is ok; a workflow runs | Not checked | | |

## Notifications

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| N1 | Live notifications arrive through Cloudflare without reloading (Server-Sent Events) | Not checked | | |
| N2 | A system alert reaches the admins (for example stop one worker for over its stale time, then start it) | Not checked | | |

## Support

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| SU1 | A user's request reaches Admin → Support; the reply reaches the user (app, notification, email) | Not checked | | |
| SU2 | An account closure request arrives as a support request and the handling procedure is written down | Not checked | | |
| SU3 | *Download account data* returns the JSON | Not checked | | |

## Monitoring

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| M1 | `curl -s http://127.0.0.1:8000/internal/metrics` on the server returns metrics (and a scraper, if used) | Not checked | | |
| M2 | journald limits installed (`deploy/journald/reelforge.conf`); `journalctl --disk-usage` reasonable | Not checked | | |
| M3 | Admin → Verification: no unexpected red item; active alerts understood | Not checked | | |
| M4 | (Optional) `tests/load_check.py` re-run on the server against a disposable database | Not checked | | |

## Legal

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| L1 | Terms of Service: every `[bracketed]` item filled in, reviewed by a lawyer for the jurisdiction | Not checked | | |
| L2 | Privacy Policy: filled in (processors, retention, contact) and reviewed | Not checked | | |
| L3 | `TERMS_VERSION` (`app/accounts.py`) set to the published version; changing it asks every user to accept again | Not checked | | |
| L4 | Footer links to /terms and /privacy on the public pages; registration requires the checkbox | Not checked | | |

## Live verification

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| LV1 | CI is green on the deployed commit (backend SQLite and PostgreSQL, migrations, frontend, end-to-end) | Not checked | | |
| LV2 | Every item of Admin → Verification's checklist ticked by the person who checked it | Not checked | | |
| LV3 | [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md) worked through | Not checked | | |

## Recovery rehearsal

| # | Check | Status | Date / by | Note |
| --- | --- | --- | --- | --- |
| R1 | `deploy/restore-check.sh --dump <newest>` passes | Not checked | | |
| R2 | Rehearsal into `reelforge_restore_test` with the off-server key copy: counts plausible, every secret decrypts | Not checked | | |
| R3 | The full restore procedure read through and the commands adapted to this server | Not checked | | |
| R4 | Scratch database dropped and the key copy shredded afterwards | Not checked | | |
