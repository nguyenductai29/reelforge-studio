# Live verification (Phase 18D)

Admin → **Kiểm định** answers two questions for system admins:

1. **Is the server ready?** Automatic, safe checks, rerun on demand.
2. **What has a person verified for real?** A checklist that only an admin ticks.

Only system admins can open it, and the API enforces that. Nothing on the page reads out a secret, sends a paid request, creates a checkout or publishes anything.

Code: `app/readiness.py`, the `/api/admin/readiness` and `/api/admin/verification` endpoints in `app/main.py`, and `frontend/src/components/reelforge/admin/admin-verification.tsx`. Migration `0017` adds `verification_checks`; Phase 19 splits the payment items per gateway.

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
| Security | Where the master key comes from (file, legacy variable, missing), the file's permissions, a differing legacy variable |
| Configuration | Settings still read from a legacy environment variable (names), whether a legacy runtime file was loaded, and the workers' settings cache (15 s). A fresh installation shows neither of the first two |
| Realtime | Open notification streams and the poll interval |
| Support | Tickets awaiting an answer |

**Kiểm tra luồng thông báo** opens the notification stream from the admin's own browser, through every proxy between the admin and the API. It passes when the first event arrives within 10 s. If it fails, check the proxy settings in [NOTIFICATIONS.md](NOTIFICATIONS.md#proxies).

The payments section shows each gateway's state (available, disabled, not configured, or an error such as an unreadable saved configuration), where its configuration comes from (admin, bootstrap, environment), OnePAY's mode, and whether the encryption key that protects admin-managed credentials is set. The gateway screen (Admin → Thanh toán → **Cổng thanh toán**) has its own checks; see [PAYMENTS.md](PAYMENTS.md#configuration-check).

## Manual checklist

The checklist has 31 items in six groups (platform, paid AI, publishing, VietQR payments, card payments, operations), stored in `verification_checks`:

- whether the item is verified;
- who verified it and when;
- an optional note, for example the video ID, order code or command output.

Nothing ticks an item automatically: not a passing readiness check, and not a saved gateway configuration either. Unticking clears who and when; the note stays.

Items marked **Tốn phí** cost money or credits. Run them deliberately, once.

| Item | How to verify | Paid |
| --- | --- | --- |
| Migration upgraded | `python -m alembic upgrade head`, then `python -m alembic current` shows `0020_manual_payment_statuses (head)`, and readiness shows Migration ok | |
| Storage on the HDD | Admin → Cài đặt hệ thống → Lưu trữ: `/srv/data/videos/reelforge` (or the legacy `REELFORGE_STORAGE_ROOT`); readiness shows the root and writable ([STORAGE.md](STORAGE.md)) | |
| FFmpeg verified | `python -m app.render_worker --check` | |
| Master key in its own file | `python -m app.master_key init`, `ls -l /etc/reelforge/master.key` shows `-rw-------`, a copy is stored off the server; Kiểm định → Bảo mật is OK ([SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md)) | |
| Gemini TTS live | `python -m app.smoke_test voice --live` ([LIVE_PROVIDER_SMOKE_TEST.md](LIVE_PROVIDER_SMOKE_TEST.md)) | yes |
| Final render live | Run a workflow that ends in Render; the MP4 plays | yes |
| Movie Recap live | The Movie Recap template on a short source ([MOVIE_RECAP.md](MOVIE_RECAP.md)) | yes |
| Article → Video live | The Article → Video template on a real article ([CONTENT_SOURCES.md](CONTENT_SOURCES.md)) | yes |
| Product Video live | The Product Video template | yes |
| YouTube upload | Publish a video as private ([MULTI_PLATFORM_PUBLISHING.md](MULTI_PLATFORM_PUBLISHING.md)) | |
| TikTok upload | Publish to TikTok; it arrives as an inbox draft | |
| Facebook Reel | Publish a Reel to a test Page | |
| Scheduled publishing | Schedule a post a few minutes ahead; it goes out and the bell reports it ([SCHEDULING.md](SCHEDULING.md)) | |
| VietQR: configuration saved | **Cổng thanh toán → VietQR**: saved with *Configured from: Admin*, the webhook URL registered with payOS, **Kiểm tra cấu hình** passes ([PAYMENTS.md](PAYMENTS.md#payos-vietqr)) | |
| VietQR: small live checkout | One small real payment; the plan activates, the order shows paid, and the owner is notified | yes |
| VietQR: webhook received | The last webhook time appears under VietQR activity | |
| VietQR: credits applied once | The studio's credit history shows the plan's credits once, even if payOS repeated the webhook | |
| Manual VietQR round trip | Manual mode: a real banking app scans the QR (bank, account, exact amount, content RF…). The buyer reports the transfer (the order shows **Chờ xác nhận chuyển khoản**, no credits yet). An admin confirms it from **Chờ xác nhận (N)** in Thanh toán. The plan activates and the credits are posted once | yes |
| Card: sandbox configured | **Cổng thanh toán → Thẻ**: Sandbox mode saved; the SANDBOX badge shows; IPN and Return URLs registered for the test merchant | |
| Card: sandbox configuration check | **Kiểm tra cấu hình** and **Kiểm tra với OnePAY (QueryDR)** both pass | |
| Card: sandbox payment succeeded | OnePAY's test card on the sandbox page; the order turns paid | |
| Card: sandbox cancellation | Cancel on the OnePAY page; the buyer lands on `payment=cancelled` and the order shows cancelled | |
| Card: IPN received | The last IPN time appears under Card activity, through the public proxy | |
| Card: QueryDR verified | **Check** on an order confirms it through QueryDR | |
| Card: production credentials configured | Production credentials saved after the confirmation; the PRODUCTION badge shows; production IPN and Return URLs registered | |
| Card: small production payment | One small real card payment | yes |
| Card: credits applied once | The credit history shows the plan's credits once | |
| Realtime notifications | **Kiểm tra luồng thông báo** passes through the public domain, and a notification arrives without reloading | |
| Support round trip | A user opens a ticket; an admin replies; the user sees the reply and the bell ([SUPPORT.md](SUPPORT.md)) | |
| Cleanup dry run | `python -m app.media_maintenance --intermediates` lists what would be deleted and deletes nothing | |
| Daily cleanup timer | `systemctl list-timers reelforge-media-maintenance.timer` shows the next run; readiness shows the last run | |

The tests never run these. Automated tests mock every provider (`python -m unittest discover -s tests`), so a green test run says nothing about live credentials. That is what this checklist is for.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/admin/readiness` | `{checked_at, sections: [{key, checks: [{key, status, detail?, …}]}]}` |
| `GET /api/admin/verification` | The 31 items: `{key, group, paid, how, verified, verified_at, verified_by, note}` |
| `PUT /api/admin/verification/{key}` | `{verified, note?}`: ticks or unticks an item; only system admins |
