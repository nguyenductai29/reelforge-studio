# Operations

Phase 13 adds the small tools needed to run ReelForge reliably on one server.

## Default models per task

**Settings → AI → Default model per task** (`GET`/`PUT /api/settings/default-models`) chooses one model each for text, image, video, voice and transcription. A step resolves its model as follows:

1. the model chosen in the step's own settings, always;
2. else the workspace default for its task, while it is enabled and supported by that step;
3. else the first enabled compatible model, in the order the workspace added them.

A default must be an enabled model of the right task in the same workspace (`422 invalid_default_model` otherwise). Disabling it later makes steps fall back to rule 3; nothing breaks. Readiness and the Run dialog show the model each step will use.

## Worker health

Every worker reports a heartbeat at most every 15 seconds into `worker_heartbeats` (migration 0014). One row per worker kind; the latest process of that kind wins. **Admin → Operations** (`GET /api/admin/workers`, system admins only) shows each worker as:

- `ok`: seen within 120 seconds;
- `stale`: not seen recently (stopped, crashed or stuck);
- `error`: seen, but its last pass failed (the scheduler reports this);
- `missing`: never started on this database.

The workers:

| Worker | Queue |
| --- | --- |
| `text_worker` | `text:` |
| `image_worker` | `image:` |
| `video_worker` | `video:` |
| `voice_worker` | `voice:` |
| `render_worker` | `render:` (renders and clip extraction) |
| `source_worker` | `source:` (web pages and transcription) |
| `youtube_worker` | `publish:youtube:` |
| `social_worker` | `publish:tiktok:`, `publish:facebook:` |
| `scheduler_worker` | none; it queues scheduled publications |

## Jobs and stuck work

`GET /api/admin/jobs` (system admins; filters `state`, `queue`, `limit`, `offset`; the response has `total`) lists recent jobs of every workspace, one page at a time. It returns safe fields only:

- queue and channel;
- state and attempt count;
- worker;
- workspace, run and step IDs;
- timestamps;
- the last error, with any URL removed.

Payloads are never returned: they can hold prompts or URLs. The response also has counts per queue and state, and a **stuck-work audit** (`jobs.stuck_jobs`):

- **expired leases**: a worker claimed the job and stopped reporting. The next worker for that queue reclaims it automatically and logs `job_lease_reclaimed` with the previous worker, which is the recovery audit trail. Paid jobs follow their own rules on reclaim: a provider call that may have happened is held for credit reconciliation, never sent again.
- **overdue**: queued for more than 10 minutes after it was due. Usually the worker for that queue is not running; check worker health.
- **orphan steps**: a step still queued or running although none of its jobs is. Inspect the run, and retry or reconcile it.

## Storage

- **Settings → Storage** (`GET /api/storage`) shows the workspace's stored bytes against its plan's quota, by kind (video, audio, image, document), with the intermediate media it can delete now. The dashboard also returns `storage`.
- Quotas come from each plan's storage limit (Phase 17). The studio sees warnings at 70, 80, 90 and 100 %, and at 100 % nothing new can be stored. See [STORAGE.md](STORAGE.md).
- `GET /api/admin/storage` lists studios fullest first, one page at a time, with the count at each warning level and the media disk's free space. Admin → Operations shows it.
- `python -m app.media_maintenance --usage` prints the same from the server.

## Media cleanup

The full policy is in [STORAGE.md](STORAGE.md). The daily job (a systemd timer at 03:00) is:

```bash
python -m app.media_maintenance --apply --intermediates   # drop --apply to preview (the default)
```

It handles four kinds of files. Links and junctions are never followed, and nothing younger than 24 hours is touched.

- **`.part` downloads** left by a crashed media worker: exact `<uuid>.part` names directly in a workspace folder (after 1 day).
- **Worker temp folders**, after 3 days:
  - `<media>/.render-tmp/<job>` (render and clip extraction);
  - `<media>/.source-tmp/<job>` (transcription audio);
  - `<media>/.publish-tmp/<job>.mp4` (the upload copy).

  Workers remove these themselves; leftovers mean a worker was killed. A folder that contains any link is skipped whole.
- **Intermediate media past retention** (`--intermediates`): scene videos, narration, generated images and extracted clips older than 30 days whose run has a final render and that no publication uses. Each is re-checked under a row lock, marked expired (its row stays with 0 bytes) and its file removed. Final renders and uploads are never removed.
- **Orphans** (`--orphans`, after 3 days): UUID-named files in a workspace folder with no `assets` row, for example when the database was restored from an older backup. The row is checked again just before deleting.

`--older-than-hours N` (at least 24) sets one age for the file leftovers. `--usage` prints each studio's bytes, quota, percent and warning level. Run a dry run first, and keep a backup of the media folder.

## Admin console (Phase 15)

The Admin page fits the window and never scrolls itself. Each tab is one table with a sticky header, a body that scrolls on its own, and pagination at the bottom. Every collection is paginated and searched on the server; the browser never downloads the full user or studio list.

| Endpoint (system admins) | Filters | Notes |
| --- | --- | --- |
| `GET /api/admin` | — | Summary only: `counts` (users, active users, admins, studios, plans, pending reconciliation, failed jobs in 24 h, pending payments, each a `COUNT` query; and stuck jobs from the stuck-work audit), the plans, and payment-provider readiness |
| `GET /api/admin/users`, `/users/{id}` | `q` (email, case-insensitive), `role` (`admin`/`member`), `status` (`active`/`locked`) | Detail adds open sessions and studios |
| `GET /api/admin/workspaces`, `/workspaces/{id}` | `q` (studio name or owner email), `plan`, `status` (`active`/`expired`/`paused`/`canceled`) | Detail adds members, counts, storage, recent ledger entries and orders |
| `GET /api/admin/payments` | `q` (email, studio, provider reference, or order code), `provider`, `status` | No checkout URL or provider payload |
| `POST /api/admin/payments/{id}/refresh` | — | Asks the order's provider server to server. There is no "mark paid" action for payOS or OnePAY orders; only a manual VietQR transfer is confirmed by hand (`/confirm`, exact amount, audited) |
| `GET /api/admin/payment-providers` | — | `{provider, method, configured}` only |

Phase 18 adds two tabs. All eight tabs (Người dùng, Studio & credits, Cấu hình gói, Thanh toán, Hỗ trợ, Đối soát credits, Vận hành, Kiểm định) still fit the window with no page scroll.

| Endpoint (system admins) | Filters | Notes |
| --- | --- | --- |
| `GET /api/admin/support`, `/support/{id}` | `q` (ticket ID prefix, subject, email, studio), `status`, `category`, `priority` | **Hỗ trợ** tab. `POST …/{id}/messages` replies; `PATCH …/{id}` sets status or priority. See [SUPPORT.md](SUPPORT.md) |
| `GET /api/admin/payment-config`, `PUT …/{provider}`, `POST …/{provider}/check\|enable\|disable` | — | Thanh toán → **Cổng thanh toán** (Phase 19). Configure VietQR and cards without SSH or restarts. Secrets are write-only and encrypted at rest; every change is audited. See [PAYMENTS.md](PAYMENTS.md#configuration-phase-19-admin-managed) |
| `GET /api/admin/readiness` | — | **Kiểm định** tab: safe local checks of the database, storage, FFmpeg, workers, AI keys, publishing, payments, realtime and support |
| `GET /api/admin/verification`, `PUT …/{key}` | — | The manual live-verification checklist, ticked only by an admin. See [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md) |

`GET /api/admin` adds `counts.support_open`: tickets waiting for support, shown in the summary row.

List responses are `{items, total, limit, offset}` (`limit` 20 by default, at most 100). `q` is matched with `LIKE` after escaping `%`, `_` and `\`, so it is always a literal substring. Account creation (`POST /api/admin/accounts`), locking (`PUT /api/admin/users/{id}`; an admin cannot lock themselves or the last active admin), plan and status changes and credit adjustments (an append-only ledger entry with a reason) are unchanged. Migration 0015 adds indexes on `workspaces.owner_id` and on `payment_orders (created_at)` and `(provider, status)` for these pages.

## System settings (Phase 20)

**Admin → Cài đặt hệ thống** holds what the server used to read from `.env.runtime`: AI provider keys, OAuth apps, storage, runtime limits, credit prices and notification timing. Values live in PostgreSQL, with secrets encrypted by the master key. The API applies a change at once; workers pick it up within 15 seconds, with no restart.

| Endpoint (system admins) | Notes |
| --- | --- |
| `GET /api/admin/system-config` | Every setting's source and value (secrets: configured or not), the master key status, redirect URLs, storage summary |
| `PUT /api/admin/system-config/{section}` | `{values, secrets, reset, confirm_root_change}`; audited by setting name |
| `POST /api/admin/system-config/ai/{provider}/test` | One free listing request with the stored key |
| `POST /api/admin/system-config/storage/check` | Whether a proposed media root is usable |

**Media root.** Changing it never moves files; the UI asks for confirmation while files exist.

**Master key.** The key file is checked by readiness (Kiểm định → Bảo mật). It warns while the legacy variable is used, when the file is readable by others, and when a remaining legacy variable differs from the file. See [SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md).

**Manual VietQR (Phases 20–21).** Transfers that buyers reported have the status `awaiting_confirmation` and appear in Admin → Thanh toán under **Chờ xác nhận (N)**. Each row shows the transfer content and when it was reported. The header counts them, and every admin gets a notification. **Xác nhận đã nhận tiền** settles the order through the shared path; **Từ chối** sets it to `rejected` and tells the owner the reason. `GET /api/admin/payments/{id}/events` lists who reported, confirmed or rejected. See [PAYMENTS.md](PAYMENTS.md#vietqr-modes-phase-20).

## Notifications and support (Phase 18)

- **Bell.** It tells users about finished, failed or blocked runs, publishing, payments, credits, storage and support replies, live over Server-Sent Events. It falls back to polling every 30 s.
- **Admins** also get new support tickets, user replies and payments that could not be applied.
- **Behind a proxy**, the stream needs buffering off on nginx and works through Cloudflare Tunnel; see [NOTIFICATIONS.md](NOTIFICATIONS.md#proxies).
- **To check it,** use Admin → Kiểm định → **Kiểm tra luồng thông báo**. The readiness view also shows how many streams are open.
- **Storage.** Notifications fire at 80, 90 and 100 % of a studio's quota (once a day per level). This is on top of the 70 % warning on the Storage page.
- **Media cleanup.** `media_maintenance --apply --intermediates` records its last run, which readiness shows. A warning after 36 h means the daily timer is not running.

## Health, metrics, alerts and the audit log (Phase 24)

| What | Where |
| --- | --- |
| Liveness | `GET /health/live` (loopback) |
| Readiness | `GET /health/ready`: database, migrations at head, master key usable; 503 names the failing check. `deploy.sh` waits for it. No paid provider is called. |
| Metrics | `GET /internal/metrics`, Prometheus text, for a scraper on the server or a signed-in system admin. Route templates only, never an email, a title or an ID. |
| Alerts | Evaluated by the scheduler worker every 5 minutes: a stale or failing worker, the media disk at 80 / 90 %, an overdue or failed backup, failed jobs, rejected payment callbacks, the master key, email failures. One in-app notification to the system admins when a condition starts, then at most every 12 hours; Admin → Kiểm định shows the active ones. `python -m app.alerts` evaluates once. |
| Audit log | Admin → Nhật ký kiểm toán: sign-ins, account and security changes, team changes, admin actions, payment callbacks rejected; filtered and paginated on the server, never a secret. |
| Request IDs | Every response has `X-Request-ID`; error bodies repeat it as `request_id`, and the log line of that request carries it. Ask a user for it when they report an error. |

Details, the rate limits and the client address behind Cloudflare: [SECURITY.md](SECURITY.md).

## Backups and recovery (Phase 25)

`reelforge-backup.timer` dumps PostgreSQL daily at 02:30 (`pg_dump` custom format, checked, chmod 600, 14 daily / 8
weekly / 6 monthly, the newest never removed). Admin → Kiểm định shows the last success, its age and the last failure.
`deploy/restore-check.sh` validates a dump, and with a scratch database and a copy of the master key rehearses the
restore. The master key is never copied next to the dumps. Procedures, media backups, log rotation and the systemd
hardening: [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md).

## Never logged or returned

Provider keys, payment-provider credentials and hash keys (including admin-managed ones), OAuth access and refresh tokens, signed media URLs, cookies and upload session URLs; since v1.0 also passwords, session tokens, email links' tokens, TOTP secrets and codes, recovery codes, the SMTP password and the Resend key. Logs carry IDs, codes and sizes; job payloads are summarized to safe fields (`app/logs.py`).
