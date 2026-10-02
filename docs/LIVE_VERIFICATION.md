# Live verification (Phase 18D; release gates since the v1.0 release closure)

Admin → **Kiểm định** (Verification) answers two questions for system admins:

1. **Is the server ready?** Automatic, safe checks, rerun on demand.
2. **What has a person verified for real?** The release gates, which only an admin records.

Only system admins can open it, and the API enforces that. Nothing on the page reads out a secret, sends a paid request, creates a checkout or publishes anything.

Code: `app/readiness.py`, the `/api/admin/readiness` and `/api/admin/verification` endpoints in `app/main.py`, and `frontend/src/components/reelforge/admin/admin-verification.tsx`. Migration `0017` adds `verification_checks`; Phase 19 splits the payment items per gateway; migration `0025_verification_status` adds the status.

On the server itself, `bash deploy/release-preflight.sh` checks what the server can tell about itself, and `bash deploy/release-report.sh` combines it with the recorded gates and CI ([V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md)).

## Readiness (automatic)

`GET /api/admin/readiness` runs every check in one pass. All checks are local: a query, a file probe, a heartbeat, or whether a variable is set. Each check is `ok`, `warning`, `error`, `missing` (needed but not configured) or `off` (an optional feature not configured).

| Section | Checks |
| --- | --- |
| Database | Connection, and whether it is PostgreSQL (SQLite is a warning). Migration: the database revision against the code's head; "behind" means run `python -m alembic upgrade head` |
| Storage | Where the media root comes from (a warning unless `REELFORGE_STORAGE_ROOT` sets it). Write probe (create and delete a temporary file). Free disk space (a warning under 10 %). Last `--apply --intermediates` cleanup (a warning if it never ran or is older than 36 h) |
| FFmpeg | `ffmpeg`/`ffprobe` found; subtitle font available |
| Workers | Each worker's heartbeat, as in Admin → Vận hành |
| AI providers | Whether each provider's variables are set (names only) and how many enabled models use it. An enabled model without its key is an error |
| Publishing | YouTube OAuth, TikTok and Facebook apps, and the token encryption key |
| Payments | payOS, manual VietQR (and the VietQR mode) and OnePAY configured, OnePAY's mode (sandbox, production or custom), and the encryption key |
| Security | Where the master key comes from (file, legacy variable, missing), the file's permissions, a differing legacy variable; since v1.0 whether the key's off-server backup is confirmed (and still matches the key in use) |
| Backups | The last successful database backup and its age (a warning past `backups.max_age_hours`), the last failure |
| Email | Whether transactional email is configured and enabled, the outbox (queued, failed recently), the last test |
| Accounts | System administrators without two-factor authentication (a warning) |
| Alerts | Active system alerts ([SECURITY.md](SECURITY.md#observability)) |
| Configuration | Settings still read from a legacy environment variable (names), whether a legacy runtime file was loaded, and the workers' settings cache (15 s). A fresh installation shows neither of the first two |
| Realtime | Open notification streams and the poll interval |
| Support | Tickets awaiting an answer |

**Kiểm tra luồng thông báo** opens the notification stream from the admin's own browser, through every proxy between the admin and the API. It passes when the first event arrives within 10 s. If it fails, check the proxy settings in [NOTIFICATIONS.md](NOTIFICATIONS.md#proxies).

The payments section shows each gateway's state (available, disabled, not configured, or an error such as an unreadable saved configuration), where its configuration comes from (admin, bootstrap, environment), OnePAY's mode, and whether the encryption key that protects admin-managed credentials is set. The gateway screen (Admin → Thanh toán → **Cổng thanh toán**) has its own checks; see [PAYMENTS.md](PAYMENTS.md#configuration-check).

## Release gates (recorded by hand)

The checklist beside readiness holds the 62 release gates of [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md) that
need a person, in eleven groups: release, platform, email, security, AI providers, render, publishing, VietQR payments,
card payments, operations, legal. For each gate an admin records:

- a **status**: *Passed*, *Failed*, *Not applicable* or *Not checked*;
- who recorded it and when (kept while only the note changes);
- a note: the order code, video ID, URL, run link or what failed. **Never a password, key or token.**

Rules:

- **Nothing is recorded automatically**: not a passing readiness check, a green pre-flight, a saved gateway, or a
  successful test. A gate is passed only when the person who checked it says so.
- *Not applicable* is offered, and accepted by the API, only for a provider an installation may leave off: payOS,
  OnePAY (every card item), Runway image and video, TikTok, Facebook. Every other gate must be *Passed*.
- *Failed* keeps the gate open; record what failed in the note, fix it, check again.
- *Not checked* clears who and when; the note stays.
- The header counts each status and says how many gates are still open. The release stays a release candidate until
  none is ([V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md)).
- Items marked **Tốn phí** (paid) cost money or credits. Run them deliberately, once, in the order below.

The automated tests never run these: they mock every provider, so a green test run says nothing about live
credentials. That is what the gates are for.

## Procedures

Work on the production server through the public site, as a system administrator, with a separate test studio (and
test accounts) for anything that creates data. Record each gate right after checking it.

### Release

| Gate | Steps |
| --- | --- |
| `release_ci_green` | On the server: `git rev-parse HEAD`. On GitHub → Actions, the run **for that exact commit**: every job green (backend on Python 3.11 and 3.14 with SQLite, backend on PostgreSQL, migrations up/down/up with `alembic check`, frontend on Node 20 and 22, Playwright). `bash deploy/release-report.sh` reads the same result from GitHub. Note the run URL. A green run on a development machine, or on another commit, does not count |
| `release_deploy` | `./deploy.sh` on the release commit ends with "ReelForge deployment completed OK" and lists every service as active. Note the commit |
| `release_preflight` | `bash deploy/release-preflight.sh --expect-commit <commit> --expect-origin https://reelforge.mul-service.com`: no FAIL. Note the WARN lines you accept |

### Platform

| Gate | Steps |
| --- | --- |
| `migration_upgraded` | `python -m alembic current` shows `0025_verification_status (head)`; readiness shows Migration ok; an account created before the upgrade still signs in |
| `storage_on_hdd` | Admin → Cài đặt hệ thống → Lưu trữ: `/srv/data/videos/reelforge`; readiness shows the root and writable; Admin → Vận hành shows its disk ([STORAGE.md](STORAGE.md)) |
| `ffmpeg_verified` | `python -m app.render_worker --check` |
| `master_key_file` | `python -m app.master_key status`: a file, `ls -l /etc/reelforge/master.key` shows `-rw-------`; a copy is stored off the server, apart from the dumps ([SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md)) |

### Email

Configure Admin → Cài đặt hệ thống → Email first (SMTP or Resend, [EMAIL.md](EMAIL.md)). Use a mailbox you can read
on another provider (for example Gmail) so that delivery is real.

| Gate | Steps |
| --- | --- |
| `email_test_sent` | **Gửi email thử**: the message arrives (not in spam). An error shows a code only (`auth_failed`, `connection_failed`…) |
| `email_dns` | In the received test message, *Show original* (Gmail) or the headers: `spf=pass`, `dkim=pass` for the sending domain, `dmarc=pass`. Publish the records the provider gives you if not |
| `email_verification` | Register a test account: the verification email arrives; its link marks the address verified |
| `email_password_reset` | Sign the test account in on a second browser. **Quên mật khẩu** on the first: the email arrives; the link sets a new password; the second browser's session ends |
| `email_password_changed` | After that reset (or a change in Settings → Security): the "password changed" email arrives |
| `email_invitation` | Invite a second test address to the test studio: the email arrives; its link joins the studio with the chosen role |
| `email_support_reply` | Reply as admin to a test support request: the email reaches the user (the reply itself stays in the app) |

Nothing here sends by itself: each email is the result of an action you take. The tests use a local SMTP sink and
never send real email.

### AI providers

Paid. Run them once each, cheapest first, in a test studio with a few credits, and only with providers you use. Note
the run ID.

| Gate | Steps |
| --- | --- |
| `gemini_text_live` | A workflow with an AI Writer step on a Gemini model (a one-paragraph idea): the step completes with text |
| `gemini_tts_live` | A Voice step on Gemini TTS (a short sentence), or `python -m app.smoke_test voice --live --text "Xin chào"` ([LIVE_PROVIDER_SMOKE_TEST.md](LIVE_PROVIDER_SMOKE_TEST.md)) |
| `transcription_live` | Upload a short clip with speech (under a minute), run a Transcript step: segments with times appear |
| `runway_image_live` | Only if Runway is used: one Image step, one image. Otherwise record *Not applicable* |
| `runway_video_live` | Only if Runway is used: one Video step, one short clip at the cheapest setting. Otherwise *Not applicable* |
| `ai_credits_once` | For the runs above: each completed step created its asset (Library), and the studio's credit history shows one reservation per step and one charge, no second charge. **Refund path, without a paid call:** in the test studio, add a model of a provider whose key is not set, run one step with it: it fails before anything is sent, and the credit history shows the reservation returned. Delete that model afterwards. `python -m app.smoke_test run-report <run_id>` compares what was sent with the run's settings |

### Render

Final renders are written under the media root (`/srv/data/videos/reelforge/<studio>/…`). Use only material you are
allowed to use.

| Gate | Steps |
| --- | --- |
| `final_render_live` | A workflow with Voice, Subtitle and a Music step (an uploaded track) feeding Render: the MP4 plays with narration, burned subtitles and the music under them; the file exists under the media root |
| `article_video_live` | The Article → Video template on a real article (a slideshow of AI images) ([CONTENT_SOURCES.md](CONTENT_SOURCES.md)) |
| `product_video_live` | The Product Video template (a slideshow) |
| `movie_recap_live` | The Movie Recap template on a short source ([MOVIE_RECAP.md](MOVIE_RECAP.md)) |
| `movie_review_live` | The Movie Review template on a short source |

### Payments

Manual VietQR first, then payOS, then OnePAY. Use the smallest plan price (set a temporary test plan if needed, and
deactivate it afterwards). **Never use a real card in an automated test**, and never in the sandbox.

| Gate | Steps |
| --- | --- |
| `bank_qr_round_trip` | In the test studio: buy a plan with VietQR (manual mode). A real banking app scans the QR: bank, account, exact amount and content `RF…` are right. Transfer, then **Tôi đã chuyển khoản**: the order shows *Chờ xác nhận chuyển khoản* (`awaiting_confirmation`), no plan change, no credits. As admin, check the bank account, then Admin → Thanh toán → **Chờ xác nhận (N)** → **Xác nhận đã nhận tiền**: the plan activates, the credits are posted once, the owner receives the `payment_succeeded` email (the receipt). Confirm again: refused (409); the credit history still shows one grant. Note the order code ([PAYMENTS.md](PAYMENTS.md#manual-vietqr)) |
| `payos_config_saved` | Only if payOS is used (else *Not applicable*): Cổng thanh toán → VietQR (payOS) saved, *Configured from: Admin*, the webhook URL registered with payOS, **Kiểm tra cấu hình** passes |
| `payos_payment` | One small real payment through the payOS page; the order turns paid |
| `payos_webhook_received` | The last webhook time appears under VietQR activity |
| `payos_credits_once` | The credit history shows the plan's credits once, even if payOS repeated the webhook |
| `onepay_sandbox_configured` | Only if cards are offered (else *Not applicable* for every card gate): Cổng thanh toán → Thẻ, Sandbox mode saved; the SANDBOX badge shows; IPN and Return URLs registered for the test merchant |
| `onepay_sandbox_check` | **Kiểm tra cấu hình** and **Kiểm tra với OnePAY (QueryDR)** pass |
| `onepay_sandbox_payment` | OnePAY's published test card on the sandbox page: the order turns paid |
| `onepay_sandbox_cancel` | Cancel on the OnePAY page: the buyer lands on `payment=cancelled` and the order shows cancelled |
| `onepay_ipn_received` | The last IPN time appears under Card activity (through the public site) |
| `onepay_querydr_verified` | **Check** on an order confirms it through QueryDR |
| `onepay_production_configured` | Production credentials saved after the confirmation; the PRODUCTION badge shows; production IPN and Return URLs registered |
| `onepay_production_payment` | One small real card payment |
| `onepay_credits_once` | The credit history shows the plan's credits once |

### Publishing

Use a test channel or Page. Note each remote ID or URL (a video ID is not a secret; a token is, and never goes in a
note).

| Gate | Steps |
| --- | --- |
| `youtube_upload` | Publish an approved render to YouTube as **private**: the publication shows published; note the video ID |
| `tiktok_upload` | If TikTok is used (else *Not applicable*): publish; it arrives in the TikTok inbox as a draft; note the publish ID |
| `facebook_reel` | If Facebook is used (else *Not applicable*): publish a Reel to the test Page; note its URL |
| `scheduled_publishing` | Schedule a post a few minutes ahead: it goes out on time and the bell reports it ([SCHEDULING.md](SCHEDULING.md)) |

### Cloudflare and security

From a machine outside the home network (for example a phone on mobile data), through
`https://reelforge.mul-service.com`.

| Gate | Steps |
| --- | --- |
| `security_headers` | The site opens over HTTPS. `curl -sI https://reelforge.mul-service.com/ \| grep -iE 'strict-transport-security\|content-security-policy\|x-content-type-options\|referrer-policy'`: HSTS, a CSP with `frame-ancestors 'none'`, `nosniff`, a Referrer-Policy. After signing in, the browser's devtools → Cookies: `rf_session` is `HttpOnly`, `Secure`, `SameSite=Strict` |
| `security_foreign_origin` | `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://reelforge.mul-service.com/api/login -H 'Origin: https://attacker.example' -H 'Content-Type: application/json' -d '{"email":"nobody@example.com","password":"x"}'` prints `403` |
| `security_client_ip` | Sign in through the tunnel; Admin → Audit log shows that sign-in with your real public address (compare with an "what is my IP" page), not `127.0.0.1`. The log shows no password, token or key |
| `security_spoofed_headers` | `curl -s -X POST https://reelforge.mul-service.com/api/login -H 'Origin: https://reelforge.mul-service.com' -H 'CF-Connecting-IP: 203.0.113.9' -H 'X-Forwarded-For: 203.0.113.9' -H 'Content-Type: application/json' -d '{"email":"<test account>","password":"wrong-password-1"}'`: the audit log's failed sign-in shows your real address, never `203.0.113.9` |
| `security_two_factor` | Every system administrator has 2FA on, with the recovery codes stored off the server (the pre-flight warns otherwise). Sign in with a code, then once with a recovery code: it works once ([SECURITY.md](SECURITY.md)) |
| `security_sessions` | Sign a second browser out from Cài đặt → Bảo mật: its next request is refused |
| `security_rate_limit` | Repeated wrong passwords on a test account end in "too many attempts" (429) and the lockout email arrives. An address with no account gets the same answer as a wrong password |
| `notification_realtime` | **Kiểm tra luồng thông báo** on Admin → Verification passes through Cloudflare, and a notification arrives without reloading |

### Backup and recovery

On the server, as the service account ([BACKUP_RECOVERY.md](BACKUP_RECOVERY.md)). Never on the production database.

| Gate | Steps |
| --- | --- |
| `backup_timer` | `sudo systemctl start reelforge-backup`, then `journalctl -u reelforge-backup -n 20`: success. `ls -ld /srv/data/backups/reelforge` shows `drwx------`; `ls -l` shows dumps `-rw-------` and no key. `systemctl list-timers reelforge-backup.timer` shows the next run. Retention reviewed (14 daily / 8 weekly / 6 monthly) |
| `backup_offsite` | The newest dump copied to another machine or encrypted disk; the key file copied to a **different** place (password manager or encrypted USB). Note where, not how to open it |
| `restore_rehearsal` | 1. `deploy/restore-check.sh --dump <newest>` passes. 2. Put the key **from its off-server copy** in `install -d -m 700 /tmp/key-copy`. 3. `createdb -O <role> reelforge_restore_test`. 4. `deploy/restore-check.sh --dump <newest> --scratch-url postgresql://<role>@127.0.0.1:5432/reelforge_restore_test --master-key /tmp/key-copy/master.key`: counts plausible against Admin's overview, the migration version is the head, every secret decrypts. 5. `dropdb reelforge_restore_test`; `shred -u /tmp/key-copy/master.key`. Note the dump name and the counts |
| `maintenance_timer` | `systemctl list-timers reelforge-media-maintenance.timer` shows the next run; readiness shows the last run |
| `cleanup_dry_run` | `python -m app.media_maintenance --intermediates` lists what it would delete and deletes nothing |

### Media backup

| Gate | Steps |
| --- | --- |
| `media_backup_verified` | `python -m app.media_manifest create --output /srv/data/backups/reelforge/media-manifest-$(date +%F).jsonl`; copy the sources and final renders elsewhere (`rsync -a /srv/data/videos/reelforge/ <copy>/`); `python -m app.media_manifest verify --manifest <manifest> --root <copy>` lists nothing missing or different |

### Support and account closure

| Gate | Steps |
| --- | --- |
| `support_round_trip` | A test user opens a support request; Admin → Hỗ trợ shows it; the admin replies; the user sees the reply, the bell and the email ([SUPPORT.md](SUPPORT.md)). Then *Request account closure* from the test user's Settings → Security: it arrives as a support request "Account closure request" |
| `alerts_delivered` | Stop one worker other than the scheduler (`sudo systemctl stop reelforge-worker@text`) for longer than its stale time: every system admin gets one in-app `system.alert` notification, and readiness lists the alert. Start it again: the alert resolves (silently) |

**Handling a closure request** (written procedure; nothing is deleted automatically, and the product makes no claim of
GDPR compliance): confirm the request comes from the account's own email; tell the user to *Download account data*
first; Admin → Người dùng → deactivate the account (this signs it out everywhere); keep payment orders, the credit
history and the audit log for accounting; reply on the request with the date; delete or anonymize anything else only
as the legal review (L2) requires, by hand, and note it on the request.

### Reboot

| Gate | Steps |
| --- | --- |
| `server_reboot` | `sudo reboot`. Afterwards, without starting anything by hand: `systemctl --failed` lists no ReelForge unit; `systemctl list-timers 'reelforge*'` shows both timers; the Cloudflare tunnel is up (the site opens); `bash deploy/release-preflight.sh` has no new FAIL; a small workflow runs to the end |

### Legal

| Gate | Steps |
| --- | --- |
| `legal_terms_reviewed` | Every `[bracketed]` placeholder of the Terms of Service filled in, in Vietnamese, English and Japanese ([V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md#legal) lists them), and the text reviewed by a lawyer for the jurisdiction. Then `TERMS_VERSION` in `app/accounts.py` set to the published version (a change asks every user to accept again). Until then the template keeps its "LEGAL REVIEW REQUIRED" marker |
| `legal_privacy_reviewed` | The Privacy Policy's placeholders filled in (processors, email provider, retention, applicable law, contact) and reviewed |

Nobody but the operator can pass these: the code ships templates, not legal text.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/admin/readiness` | `{checked_at, sections: [{key, checks: [{key, status, detail?, …}]}]}` |
| `GET /api/admin/verification` | `{items: [{key, group, paid, optional, how, status, recorded_at, recorded_by, note, verified, verified_at, verified_by}], summary: {passed, failed, not_applicable, not_checked, total, open, complete}}`; `verified*` repeat the passed state for older clients |
| `PUT /api/admin/verification/{key}` | `{status, note?}` with `status` one of `passed`, `failed`, `not_applicable` (optional gates only, else 422), `not_checked`; the older `{verified, note?}` still works (true: passed, false: not checked). Only system admins; audited as `admin.verification_updated` with the status (never the note) |
