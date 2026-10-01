# Final product audit (Phases 14–21)

> Snapshot: branch `feat/studio-foundation`, after Phases 14–21, 2026-10-01. Database head: `0020_manual_payment_statuses`.

**Production readiness (Phase 21).**

- **Bootstrap:** a fresh installation runs with only the database URL and `/etc/reelforge/master.key`; no `.env.runtime`.
- **Environment variables:** every one the backend still reads is classified (bootstrap, legacy fallback, experimental, dev) and enforced by a test.
- **No restarts:** a running worker picks up a changed provider key, VietQR mode or card switch without a restart.
- **Services:** systemd units (`deploy/systemd/`) need no provider secret, and `deploy.sh` refuses to restart without a usable master key.

See [PRODUCTION_BOOTSTRAP.md](PRODUCTION_BOOTSTRAP.md) and [SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md).

**Rule applied:** every control in the production interface works. An unfinished feature was either built now, when it reuses existing capabilities cheaply, or removed from the interface. No "coming soon" badge, banner, disabled placeholder switch or preview-only template remains.

`tests/test_product_audit.py` enforces this. It fails on:

- a `SoonBadge` or `ComingSoonBanner`;
- a "coming soon" string in any language;
- a hard-coded disabled switch;
- a step-library entry or preset the backend cannot run;
- a template without a backend graph;
- Instagram in the channel lists.

## 1. Implemented features

**Content creation**

- **Templates** (`/create`). Each one creates a workflow that the backend builds and runs:
  - Social video;
  - YouTube Short and YouTube (16:9);
  - TikTok video;
  - Facebook Reel;
  - Repurpose existing content;
  - Movie Recap;
  - **Movie Review**: Movie Recap with the review style and light spoilers;
  - **Article → Video**: URL source → AI Writer → scenes → AI image per scene → voice → subtitles → render;
  - **Product Video**: idea → 30-second promo script → scenes → images → voice → subtitles → render;
  - Blank.
- **Step library**: 47 entries, all executable. Several are presets of an existing step rather than new node types:

  | Entry | Step it adds |
  | --- | --- |
  | Short script | AI Writer, 60 s, YouTube Shorts |
  | Long script | AI Writer, 600 s, YouTube |
  | Movie review | Recap Script, review style, light spoilers |
  | Ending explained | Recap Script, explainer style, full spoilers |
  | Thumbnail | Image, 16:9, high quality |
  | Key moments | Story Analysis |
  | Text to video | Video |
  | Merge clips | Render |
  | Preview | Review |
  | Schedule post | Publish (scheduling happens in the publish dialog) |
  | Upload image | Media source (images are accepted) |

- **Background Music** (`music` step). It takes an MP3, WAV or OGG file that the user uploaded and owns:
  - Render mixes it under the narration at 1–100 % volume (default 15 %);
  - a track shorter than the video either loops or plays once (`mode`);
  - a longer track is cut where the video ends.
- **Image slideshows.** Render accepts still images as scenes. Each image shows for:
  - its scene's narration;
  - else a share of the single narration;
  - else `RENDER_STILL_SECONDS` (default 5).
- **Workspace content defaults**: platform, tone and length, used by writing steps that leave them empty.

**Library, media and storage**

- The Library **Scripts** tab lists every run's AI-written scripts, one page at a time. Each project shows its latest script.
- **Add to project** for uploads.
- **Media page:**
  - filter by project;
  - select several files and **delete** them after confirming;
  - delete the open file.
- **Storage:**
  - Settings → Storage shows used / quota / percent with warnings at 70, 80, 90 and 100 %;
  - a banner appears from 80 %;
  - "Delete intermediate media" removes media of all projects or one project, after a preview and a confirmation.

**Billing**

- VietQR / Bank transfer (payOS) and Bank card (OnePAY). Only configured methods appear.
- Paginated order history: plan, method, amount, status, created, paid, order code.
- Plan cards show their storage.

**Notifications and support** (Phase 18)

- **Notification bell** in the header, between the generation center and the account menu:
  - an unread badge (up to "99+");
  - the latest notifications, mark read, mark all read;
  - `/notifications` with All/Unread and pagination.
- **What it reports:** runs that complete, fail, need attention or wait for review; publishing (scheduled, published, failed, needs attention); payments; low credits and admin adjustments; storage at 80/90/100 %; support replies.
- **Realtime:** Server-Sent Events, with polling as the fallback. See [NOTIFICATIONS.md](NOTIFICATIONS.md).
- **Support:** account menu → **Hỗ trợ**: the user's requests, a new request with a category, and a thread with replies from "ReelForge support". See [SUPPORT.md](SUPPORT.md).

**Administration**

The console fits the window and never scrolls; each table body scrolls inside it, with sticky headers and pagination always visible. It has nine tabs:

| Tab | What it shows and does |
| --- | --- |
| Users | Search, role and status filters; create account in a dialog; view, lock, unlock |
| Studios & credits | Search, plan and status filters, a **storage** column; view, change plan, adjust credits, pause, activate |
| Plans | Name, price, limits, monthly credits, **storage limit**, and whether the plan is purchasable (and why not: inactive, no price, no gateway enabled) |
| Payments | Search, provider and status filters; view; refresh provider state; **Cổng thanh toán** (Phase 19): configure VietQR (payOS) and cards (OnePAY): write-only secrets encrypted at rest, enable/disable, OnePAY Sandbox/Production/Advanced with a persistent mode badge and a production confirmation, source (Admin/Bootstrap/Environment/Missing), why a gateway is unavailable, callback URLs, activity, change history, a safe check |
| Support | Search, status, category and priority filters; open a ticket, reply, change status or priority, resolve, close |
| Credit reconciliation | The Phase 3.7 review of held credits |
| Operations | Worker heartbeats, the job table, the stuck-work audit, and storage: disk free space, studios per warning level, fullest studios |
| Verification | Readiness checks (database, migration, storage, FFmpeg, workers, AI keys, publishing, payments, realtime, support, security: the master key, configuration: settings still from the environment and a loaded runtime file), a browser stream check, and the 31-item manual live checklist, grouped (payments split per gateway) |
| System settings (Phase 20) | Security (master key status), General, AI providers (switch, write-only key, connection test), Social OAuth (app credentials, derived redirect URLs), Storage (validated root, no silent moves, ceiling, retention), Runtime, Credit pricing, Notifications; every value's source (Admin / Environment / Default) |

**Publishing and scheduling**: see § 5 and § 6.

## 2. Hidden/deferred features

They are not shown anywhere in the production interface.

| Feature | Decision | Why |
| --- | --- | --- |
| **Instagram** publishing | Hidden | Needs an Instagram professional account linked to a Page, `instagram_content_publish`, its own container/publish flow and Meta review: new scope beyond the Facebook Page integration |
| YouTube URL input | Hidden | Downloading YouTube media conflicts with YouTube's terms; users upload media they own |
| Research, Scene planner, Storyboard | Hidden | No backend; AI Writer, Story Analysis and Scene Splitter cover the released flows |
| Stock media | Hidden | Needs a licensed stock provider |
| Image → video | Hidden | No image-conditioned video provider is integrated |
| Voice clone | Hidden | Consent, abuse and provider work are out of scope |
| Sound effects, Audio mixer | Hidden | Background Music covers the released need |
| Crop/resize, Aspect ratio, Overlay text, Transition, Timeline | Hidden | They need an editing timeline; Render already sets the aspect ratio |
| Download step | Hidden | Not a pipeline step. Final videos download from Library, Media and the project page |
| `/ai/*` single-tool pages | Redirect to `/create` | The same features are templates |
| Workspace brief chips, AI rewrite, storyboard tab, assistant panel | Removed | No backend |
| Settings: teammates, 2FA, "auto-schedule at best time", recap/thumbnail/provider-ID toggles | Removed | Larger features, or no behavior |
| Automatic deletion of uploads | Not offered | Never without an explicit user action (§ 9) |
| Legacy `script` node | Opens only in old workflows | A run blocks it with a reason |
| Crypto payments | Not offered | Out of scope |

## 3. Billing providers

| Provider | Method | Credentials | Where | Callback URLs |
| --- | --- | --- | --- | --- |
| payOS | VietQR / Bank Transfer | Client ID, API Key, Checksum Key | Admin → Cổng thanh toán (encrypted); legacy fallback: `payos` object in `instance/bootstrap.json` | Webhook `https://<frontend>/api/webhooks/payos` |
| OnePAY | Credit / Debit Card | Merchant ID, Access Code, Hash Key (hex); recommended QueryDR User and Password; mode Sandbox / Production / Advanced | Admin → Cổng thanh toán (encrypted); legacy fallback: `ONEPAY_*` in the runtime environment | IPN `https://<frontend>/api/webhooks/onepay`; return `https://<frontend>/api/billing/onepay/return` |

**Configuration (Phase 19).** `app/payment_config.py` resolves every provider's configuration:

- **Precedence:** the admin-managed configuration first, then the legacy bootstrap/env one.
- **Errors are not hidden:** a saved configuration that cannot be decrypted is an error, never a silent fallback.
- **Immediate:** saved changes apply on the next request, with no restart.
- **Availability:** a provider is offered to buyers only when enabled and valid. Disabling never strands a pending order: callbacks and checks still use its credentials.
- **Encryption:** credentials are one Fernet ciphertext per provider, keyed by `REELFORGE_TOKEN_ENCRYPTION_KEY` through an HKDF-derived per-purpose key (`app/secret_box.py`).
- **Audit:** every change and test is recorded in `payment_config_audit` by field name.

**How settlement works**

- Both providers settle through `payments.settle` → `apply_paid`.
- `apply_paid` checks that the evidence comes from the provider that created the order and that the amount matches exactly.
- It extends the subscription and posts the credits once, under a row lock.

**Never trusted on its own**

- A browser return never pays an order. Only OnePAY's signed IPN, or a server-side QueryDR check, can.
- No admin "mark paid" action exists.
- Credentials are write-only. Once saved, no API, error, log or audit entry returns them; the admin view shows field statuses, with only the payOS client ID and the OnePAY merchant ID masked. It is system-admin only, enforced by the API.
- ReelForge never collects or stores card data: both methods pay on the provider's hosted page.
- The admin check never charges: a local validation, and for OnePAY one QueryDR about a reference that cannot exist.

See [PAYMENTS.md](PAYMENTS.md).

## 4. Admin architecture

- `GET /api/admin` returns `COUNT`-based summary counts, `storage_levels`, plans and payment-provider readiness. It never returns user or studio lists.
- These endpoints page and filter on the server:
  - `/api/admin/users`;
  - `/api/admin/workspaces` (with `storage` per studio);
  - `/api/admin/payments`;
  - `/api/admin/jobs`;
  - `/api/admin/storage`;
  - `/api/admin/reconciliation`;
  - `/api/admin/support` (Phase 18).
- Phase 18 adds `/api/admin/payment-config` (and `/check`), `/api/admin/readiness` and `/api/admin/verification`. Phase 19 adds `PUT /api/admin/payment-config/{provider}` and `POST …/{provider}/check|enable|disable`. They are system-admin only, same-origin protected for changes, and never return a secret.
- `q` is a case-insensitive literal substring (`LIKE` with `%`, `_` and `\` escaped).
- List responses are `{items, total, limit, offset}`, with `limit` 20 by default and at most 100.
- The frontend's `DataTable` (`frontend/src/components/reelforge/data-table.tsx`) provides the toolbar, loading and empty states, sticky header, internal scroll, pagination and horizontal scroll.

See [OPERATIONS.md](OPERATIONS.md#admin-console-phase-15).

## 5. Social publishing

| Channel | How | Approval |
| --- | --- | --- |
| YouTube | Google OAuth `youtube.upload`, resumable upload | Unverified Google API projects upload only private videos |
| TikTok | Login Kit + Content Posting API (`video.upload`), inbox drafts | App approved for `video.upload` |
| Facebook | Facebook Login, Page Reels (`pages_show_list`, `pages_read_engagement`, `pages_manage_posts`) | Meta app review for accounts outside the app's roles |
| Instagram | Hidden (§ 2) | — |

- One approved video can go to several channels. Each channel gets its own metadata, publication and upload job.
- Tokens are encrypted with `REELFORGE_TOKEN_ENCRYPTION_KEY`.
- Uncertain uploads become `needs_attention`; they are never reported as a false success.

See [MULTI_PLATFORM_PUBLISHING.md](MULTI_PLATFORM_PUBLISHING.md).

## 6. Scheduling

- A publication can be scheduled (UTC), moved or cancelled until its upload starts. `python -m app.scheduler_worker` queues it on time.
- The Calendar loads only the visible date range and shows scheduled, queued, uploading, succeeded, failed and cancelled items.
- The default publishing time (Settings → Publishing) prefills the dialog.

See [SCHEDULING.md](SCHEDULING.md).

## 7. Storage policy

See [STORAGE.md](STORAGE.md).

- **One root.** `REELFORGE_STORAGE_ROOT`, else the `storage_dir` setting. Files live at `<root>/<workspace_id>/<asset_id>`; paths contain IDs only and never reach users.
- **Kinds.** Every asset has a `kind`: `source`, `generated_image`, `scene_video`, `voice`, `subtitle`, `extracted_clip`, `final_render` or `other`. It is set where the asset is created; older assets were labelled from their step's node type by migration 0016.
- **Expired or deleted media** keeps its row (lineage, run history, publications) with `bytes = 0`, `expired_at`, `expired_reason` and `expired_bytes`. Its file is removed, and downloading it answers 410 `media_expired`.
- **Users can delete:**
  - files on the Media page;
  - the intermediate media of all projects or one project, from Settings → Storage.

  The workspace owner confirms each deletion. Media that an unfinished publication needs is kept.

## 8. Quota rules

| Plan | Storage limit (default, editable in Admin → Plans) |
| --- | --- |
| Trial | 1 GB |
| Standard | 10 GB |
| Pro | 30 GB |

- `WORKSPACE_MEDIA_QUOTA_BYTES`, when set, caps every plan. It is also the limit of a plan without one (1 GiB if unset).
- Usage counts uploads, generated images, scene videos, narration, subtitles, render outputs and extracted clips.
- Enforcement happens before anything is stored, under the studio's row lock:
  - uploads are refused with 413;
  - image, voice and video jobs are refused before the provider call, and their credits are refunded;
  - Render, Extract Source Clips and Subtitle are blocked.

  Concurrent uploads cannot both use the last room.
- **Warning levels:**

  | Usage | Level |
  | --- | --- |
  | ≥ 70 % | notice |
  | ≥ 80 % | warning (banner on every page) |
  | ≥ 90 % | critical |
  | ≥ 100 % | full: no new media |

  Reading, downloading and publishing keep working at every level.

## 9. Retention rules

| What | Kept | Variable |
| --- | --- | --- |
| Final renders | **Always** | — |
| Uploaded sources | **Until the user deletes them** | — |
| Subtitles, unclassified assets | Always | — |
| Scene videos, narration, generated images, extracted clips | 30 days, and only once their run has a final render and no publication uses them | `REELFORGE_RETENTION_INTERMEDIATE_DAYS` (0 = keep) |
| Worker scratch folders | 3 days | `REELFORGE_RETENTION_TEMP_DAYS` |
| `.part` files | 1 day | `REELFORGE_RETENTION_PARTIAL_DAYS` |
| Orphan files (`--orphans`) | 3 days | `REELFORGE_RETENTION_ORPHAN_DAYS` |

**Daily cleanup** at 03:00 (systemd timer; [STORAGE.md](STORAGE.md#schedule-it-daily-at-0300-systemd)):

```bash
python -m app.media_maintenance --apply --intermediates
```

- It is a dry run without `--apply`.
- A file is deleted only at `<root>/<workspace_id>/<asset_id>`, as a regular file reached through no link.
- Each asset is re-checked under a row lock before it is marked expired.
- An expired row whose file survived an interrupted run is swept on the next run.

## 10. Required workers

| Process | Needed for |
| --- | --- |
| API (`uvicorn app.main:app`) and Next.js frontend | Always |
| `python -m app.text_worker` | Every writing step |
| `python -m app.image_worker` | Image steps, including slideshow templates |
| `python -m app.video_worker` | AI video clips |
| `python -m app.voice_worker` | Narration |
| `python -m app.render_worker` | Render (music, slideshows) and source-clip extraction |
| `python -m app.source_worker` | URL sources and transcription |
| `python -m app.youtube_worker` | YouTube uploads |
| `python -m app.social_worker` | TikTok and Facebook uploads |
| `python -m app.scheduler_worker` | Scheduled publications |
| `reelforge-media-maintenance.timer` | Daily cleanup at 03:00 |

Payments, notifications and support need no worker: the API serves the notification stream. Admin → Operations shows each worker's heartbeat.

## 11. Bootstrap and configuration

**Since Phase 20, production needs only:**

- `instance/bootstrap.json` with the database URL;
- the master key file `/etc/reelforge/master.key` (chmod 600; `python -m app.master_key init`).

**Everything else is in the admin UI:**

- **Admin → Cài đặt hệ thống:** AI providers, social OAuth, storage, runtime, credit pricing, notifications.
- **Admin → Thanh toán → Cổng thanh toán:** VietQR manual/payOS, OnePAY.

Values are stored in PostgreSQL (secrets encrypted) and picked up without restarts. See [SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md).

**Legacy variables.** The variables below remain a fallback for settings nobody saved in the admin UI; a saved value always wins. They live in `/etc/reelforge/runtime.env` (optional, `EnvironmentFile=-…`) or in `.env.runtime` in development. A blank `KEY=` line means "use the default". Only the logging variables, `REELFORGE_MASTER_KEY_FILE`, the live smoke-test choices and the experimental `DOLA_*` stay environment-only.

| Area | Variables |
| --- | --- |
| Text, transcription, voice | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` |
| Image and video | `RUNWAYML_API_SECRET`, `RUNWAY_OUTPUT_HOSTS`, `FAL_KEY`, `RUNWARE_API_KEY`, `REPLICATE_API_TOKEN` (`DOLA_*` experimental) |
| Credits and limits | `*_CREDITS_PER_*`, `*_JOB_MAX_AGE_SECONDS`, `TRANSCRIPTION_MAX_SECONDS` |
| Rendering | `RENDER_FFMPEG_PATH`, `RENDER_FFPROBE_PATH`, `RENDER_SUBTITLE_FONT`, `RENDER_TIMEOUT_SECONDS`, `RENDER_STILL_SECONDS` |
| Storage | `REELFORGE_STORAGE_ROOT`, `WORKSPACE_MEDIA_QUOTA_BYTES`, `REELFORGE_RETENTION_INTERMEDIATE_DAYS`, `REELFORGE_RETENTION_TEMP_DAYS`, `REELFORGE_RETENTION_PARTIAL_DAYS`, `REELFORGE_RETENTION_ORPHAN_DAYS` |
| YouTube | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` |
| TikTok | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`, `TIKTOK_APPROVED_SCOPES` |
| Facebook | `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`, `FACEBOOK_REDIRECT_URI` |
| Master key | `/etc/reelforge/master.key` (or `REELFORGE_MASTER_KEY_FILE`); legacy `REELFORGE_TOKEN_ENCRYPTION_KEY`. Encrypts OAuth tokens and every admin-managed secret. Back it up; never change it |
| Card payments (legacy fallback; normally configured in Admin) | `ONEPAY_MERCHANT_ID`, `ONEPAY_ACCESS_CODE`, `ONEPAY_HASH_KEY`, `ONEPAY_QUERY_USER`, `ONEPAY_QUERY_PASSWORD`, `ONEPAY_PAYMENT_URL`, `ONEPAY_QUERY_URL` |
| Notifications | `REELFORGE_SSE_POLL_SECONDS` (3), `REELFORGE_SSE_MAX_SECONDS` (300), `CREDITS_LOW_THRESHOLD` (20) |
| Logs | `REELFORGE_LOG_FORMAT`, `REELFORGE_LOG_LEVEL` |

The database URL stays in `instance/bootstrap.json`; payOS keys there are a legacy fallback.

## 12. DB migration head

`0020_manual_payment_statuses`. Migrations 0001–0019 are unchanged.

| Migration | Adds | Test |
| --- | --- | --- |
| `0015_admin_payments_profiles` | `user_profiles` (display name); indexes for the admin and payment pages | `tests/test_admin_payments_migration.py`: 0014 → 0015 and back |
| `0016_storage_lifecycle` | `plans.storage_limit_bytes` (Trial 1, Standard 10, Pro 30 GiB); `assets.kind` (back-filled from each asset's step); `assets.expired_at`, `expired_reason`, `expired_bytes`; index `ix_assets_kind_created_at` | `tests/test_storage_migration.py`: 0015 → 0016 and back |
| `0017_notify_support_verify` | `notifications` (integer IDs, which are also the stream's event IDs; unique `(user_id, dedupe_key)`; indexes `(user_id, created_at)` and `(user_id, read_at)`); `support_tickets` and `support_messages` (categories, statuses, priorities and author types checked); `verification_checks` | `tests/test_phase18_migration.py`: 0016 → 0017 and back (SQLite, and PostgreSQL 16 with `REELFORGE_TEST_DATABASE_URL`); every revision ID fits PostgreSQL's 32-character version column |
| `0018_admin_payment_config` | `payment_provider_configs` (one row per provider: `enabled`, `mode`, `config_ciphertext`, who and when; no secret column); `payment_config_audit` (action, admin, time, field names) | `tests/test_phase19_migration.py`: 0017 → 0018 and back, keeping orders, subscriptions, ledger and `payment_activity` (SQLite, and PostgreSQL with `REELFORGE_TEST_DATABASE_URL`) |
| `0019_system_configuration` | `system_config` (one row per admin-managed setting: JSON value or ciphertext, never both); `system_config_audit`; `payment_orders.transfer_reported_at`; `payment_order_events` (manual VietQR reported/confirmed/rejected) | `tests/test_phase20_migration.py`: 0018 → 0019 and back (SQLite, and PostgreSQL with `REELFORGE_TEST_DATABASE_URL`) |
| `0020_manual_payment_statuses` | Data only: manual VietQR orders the buyer reported become `awaiting_confirmation`, rejected ones `rejected`; no schema change | `tests/test_phase21.py`: 0019 → 0020 and back (SQLite, and PostgreSQL with `REELFORGE_TEST_DATABASE_URL`) |

The tests keep every existing row. Set `REELFORGE_TEST_DATABASE_URL` to also run them on PostgreSQL. Apply with `python -m alembic upgrade head` before restarting the services.

## 13. FFmpeg requirements

- System `ffmpeg` and `ffprobe` (`RENDER_FFMPEG_PATH` / `RENDER_FFPROBE_PATH`), plus Noto fonts for burned-in subtitles. On Ubuntu: `sudo apt install -y ffmpeg fonts-noto-core fonts-noto-cjk`.
- Check with `python -m app.render_worker --check`.
- They are needed by the API, the render worker (renders and clip extraction) and the source worker (transcription).

## 14. Recommended home-server directories

| Disk | Holds |
| --- | --- |
| SSD 256 GB | OS, application, virtualenv, PostgreSQL, Docker/system files |
| HDD 1 TB, mounted at `/srv/data` | Media and backups |

```
/srv/data/
├── backups/reelforge/   daily pg_dump (copy it off the HDD too)
├── images/              other projects
├── uploads/             other projects
└── videos/reelforge/    REELFORGE_STORAGE_ROOT: all ReelForge media
```

- PostgreSQL metadata is small next to the videos.
- A backup on the same HDD does not survive that disk failing.
- Do not duplicate the video tree on the same disk by default.

See [home-server-deployment.md](home-server-deployment.md) § 11.

## 15. Live verification requirements

All Phase 14–20 tests are offline; no paid or live API was called. An operator must verify the following, then tick it in Admin → Kiểm định ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)).

**Payments**

- Configure both gateways in Admin → Cổng thanh toán, with no SSH.
- Manual VietQR: scan the QR with a real banking app, transfer a small amount, report it, confirm it in Admin; the credits are posted once.
- Move the master key into `/etc/reelforge/master.key` and back it up.
- OnePAY sandbox, then one real payment ([PAYMENTS.md](PAYMENTS.md#sandbox--production-onepay)):
  - checkout;
  - return;
  - IPN through the proxy (`responsecode=1`);
  - QueryDR;
  - a cancelled payment;
  - credits posted once.
- payOS: one payment after deployment, with the webhook received.
- Confirm the sandbox endpoints (`mtf.onepay.vn`) against OnePAY's integration guide.

**Rendering**

- A real FFmpeg render of an image slideshow with Background Music: volume, loop or play-once, still timing.
- Movie Recap and Movie Review on a real source video.

**Templates**

- One live run each of Article → Video and Product Video. They use paid text, image and voice providers.

**Storage**

- On the real HDD:
  - set `REELFORGE_STORAGE_ROOT`;
  - run `python -m app.media_maintenance --intermediates` (dry run) and read its list;
  - enable the timer;
  - after the first 03:00 run, check `journalctl -u reelforge-media-maintenance` and that final videos still play.
- Check that the disk free space shows in Admin → Operations.

**Publishing**

- Real TikTok inbox and Facebook Reel uploads.
- Scheduled publishing on time.

**Notifications and support**

- **Kiểm tra luồng thông báo** passes through the public domain (Cloudflare Tunnel, and nginx if used).
- A notification arrives without reloading.
- One support round trip between a user and an admin.

**UI**

The Admin layout, card checkout and storage screens were checked in headless Chromium on an isolated stack:

- Admin at 1366×768, 1680×1050 and 390×844: the page never scrolls, table bodies scroll inside it, headers stay sticky, pagination stays visible.
- Card checkout against a fake gateway that was never reached.
- Phase 18, against an isolated API and Next.js server:
  - a notification reached the bell through the Next.js proxy in under a second;
  - the support round trip worked;
  - the payment setup showed no secret;
  - the stream check passed;
  - the checklist persisted;
  - none of the eight Admin tabs scrolled at 1366×768;
  - the bell did not overflow at 360 px.
- Phase 19, same isolated stack:
  - both gateways were configured from the admin UI;
  - no secret appeared in the page or in any API response, and the inputs were empty after saving;
  - the masked client ID showed;
  - the SANDBOX/PRODUCTION badges showed, and production needed the confirmation;
  - Billing showed "VietQR / Chuyển khoản" and "Thẻ tín dụng / ghi nợ" without provider names, and disabling VietQR removed it at once;
  - plans showed why they were not purchasable;
  - Admin still did not scroll.
  - Nothing was sent to payOS or OnePAY.

Real devices remain to be checked.

**Providers**

- Operator-verified: Gemini text, Runway `gen4.5` and `gen4_image`.
- Mocked only: the rest (see `IMPLEMENTATION_STATUS.md` → Live Provider Verification).

## 16. Known limitations

**Payments**

- Manual VietQR depends on an administrator checking the bank account; there is no bank statement integration.
- Card payment has not been tested live.
- Changing the encryption key makes saved gateway credentials unreadable; they must be re-entered. There is no key rotation.
- One configuration per provider: no separate sandbox and production profiles to switch between.
- Without QueryDR credentials, a card order waits for the IPN.
- Refunds are handled outside the app.
- Renewal is manual.

**Music and slideshows**

- One looped (or play-once) track per render; no ducking curve or fades.
- Stills have no motion or transitions.
- Product Video uses AI images; uploaded product photos are connected manually through a media source.

**Storage**

- One root only.
- One retention period for all intermediate kinds.
- Sources are never removed automatically.
- Plan limits can add up to more than the disk. Watch the disk line in Admin → Operations, and use `WORKSPACE_MEDIA_QUOTA_BYTES` as a ceiling.
- A run whose intermediates expired cannot be re-rendered; start a new run.

**Notifications and support**

- In-app only: no email or push notifications.
- Each open stream checks the database every few seconds, which is fine for a home server and would need a pub/sub channel for thousands of concurrent users.
- Support has no attachments and no assignment to a particular admin.

**Admin**

- User search matches email only.
- `LIKE '%q%'` scans the table: fine for thousands of rows, and would need a trigram index for many more.

**Scope**

- No team collaboration, 2FA, password recovery or email delivery.
- payOS keys cannot be proven without a real payment link; verify them with one small payment.
- TikTok uploads are inbox drafts.
- YouTube uploads from an unverified Google project stay private.

## Remaining SoonBadge / ComingSoonBanner

**Count: 0.** Both components were deleted from `frontend/src/components/reelforge/primitives.tsx`. No source file, dictionary or test refers to them, and `tests/test_product_audit.py` fails if one comes back.

The only other copies are in `frontend/.next-dev/`, the git-ignored build cache of a running `next dev` server. They are not shipped.
