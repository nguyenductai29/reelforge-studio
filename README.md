# ReelForge Studio

Self-hosted foundation for a short-video production platform. Next.js/React/TypeScript powers the dashboard, while FastAPI/Python serves the API. The implemented path is project topic → one AI-generated MP4 → manual review → private YouTube upload. Video jobs support fal, Runware, Replicate and direct Runway Dev, plus an opt-in experimental Dola gateway; PostgreSQL stores queue leases, credits, asset lineage and per-channel publication state. Facebook/TikTok upload adapters exist, but account connection, publication jobs and scheduling for those channels are not wired into the app. Multi-scene rendering and analytics are still planned. External provider and social-account calls still need validation with real credentials.

## Start locally with PostgreSQL

Requires Python 3.11+, Node.js 20.9+ and an existing PostgreSQL database. Create a dedicated database and user on your PostgreSQL server. Give the user permission to create tables in its own database/schema.

1. Create the local `instance` directory and copy `config.example.json` to `instance/bootstrap.json`. The template contains the requested host, user, database and query string. Replace the literal `PASSWORD` with the **actual password on your server**; it is only a placeholder in the repository. URL-encode the password if it contains reserved URL characters. This is the **only backend bootstrap value outside PostgreSQL**: the app cannot discover a database connection by reading that database. Keep the file out of Git and restrict access to the service account. Do not put the database URL in an environment file; provider keys go in `.env.runtime` (below).
2. Install dependencies and run the initial migration **before** starting the API. From the repository root:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

3. Start the frontend in a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

The dev server writes generated files to `frontend/.next-dev`; `npm run build` and `npm start` use `frontend/.next`. Keeping these directories separate prevents a production build from replacing chunks used by a running dev server. Both directories are generated and ignored by Git.

Open http://localhost:3000 to create the first admin account, or run `npm run create-admin` in `frontend` while the API is running (it prompts for the email and password, or reads `ADMIN_EMAIL` and `ADMIN_PASSWORD`). On a public server, create the admin this way before the site is reachable; until an account exists, the first visitor can claim it. API documentation is at http://127.0.0.1:8000/docs. The Next.js proxy defaults to `http://127.0.0.1:8000`; if the API is at a different server address, copy `frontend/config.example.json` to `frontend/instance/config.json` and set `api_base_url` to the address **reachable by the Next.js server**. That address is the frontend's connection bootstrap, not an application preference.

### Provider keys, processes and logs

Provider keys and prices are environment variables that the API and every worker must share: a worker that finishes one step also starts the next (the text worker queues the video step after an AI Writer). Copy `.env.runtime.example` to `.env.runtime` (git-ignored) and fill in only the providers you use. The API loads it at startup, and so do `python -m app.text_worker`, `app.image_worker`, `app.video_worker`, `app.voice_worker`, `app.render_worker`, `app.source_worker`, `app.youtube_worker`, `app.social_worker`, `app.scheduler_worker`, `app.provider_check` and `app.smoke_test`. A variable already set in the process wins, and `REELFORGE_ENV_FILE` names another file (production uses one `EnvironmentFile=` for every systemd unit). Start each process in its own terminal:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # API
python -m app.text_worker                                      # AI Writer, Summarize, … steps
python -m app.image_worker                                     # image steps
python -m app.video_worker                                     # video steps (one clip, or one per scene)
python -m app.voice_worker                                     # voice (text-to-speech) steps
python -m app.render_worker                                    # render steps and Movie Recap clips (needs FFmpeg)
python -m app.source_worker                                    # URL Source and Transcript steps
python -m app.youtube_worker                                   # only for YouTube publishing
python -m app.social_worker                                    # only for TikTok and Facebook publishing
python -m app.scheduler_worker                                 # only for scheduled publishing
cd frontend && npm run dev                                     # dashboard
```

Each process logs structured JSON lines to stderr. The first is `process_started`, with the runtime file it loaded and an 8-character fingerprint per provider key, so a process started with different keys stands out. Run events follow: `workflow_step_*`, `job_claimed`, `provider_request_*`, `credit_reserved` and `credit_refunded`, with run, step, job, provider and model IDs. Keys are redacted and prompts are logged by length only. `REELFORGE_LOG_FORMAT=text` switches to `key=value` lines, and `REELFORGE_LOG_LEVEL` sets the level.

`python -m app.provider_check` reports, without calling any provider, whether the selected text and video providers (and, with `--only image` or `--only voice`, the image or voice provider) have their keys, supported models and settings. `python -m app.render_worker --check` reports FFmpeg, ffprobe and the subtitle font. `python -m app.smoke_test text|video|image|voice --live` sends one small paid request, and `python -m app.smoke_test run-report <run_id>` checks that a run's requests match its node settings. See [docs/LIVE_PROVIDER_SMOKE_TEST.md](docs/LIVE_PROVIDER_SMOKE_TEST.md), including the full workflow test and troubleshooting.

### Studio interface

The dashboard follows the ReelForge Studio design: a dark, cool near-black theme with one cyan-blue accent, built with Tailwind CSS v4 and shadcn/ui (Radix) components under `frontend/src/components/ui`. The theme tokens live in `frontend/src/app/globals.css`. Space Grotesk sets headings and DM Sans sets interface copy; DM Sans has no Vietnamese glyphs, so the Vietnamese interface uses Be Vietnam Pro, which also backs up Vietnamese titles in other languages. All three are self-hosted npm packages (`@fontsource*`), so builds need no font download.

Each module has its own URL: `/` (home), `/create`, `/projects`, `/projects/<id>`, `/workspace/<id>`, `/workflows`, `/workflows/<id>`, `/library`, `/media`, `/publishing`, `/calendar`, `/models`, `/channels`, `/billing`, `/settings`, `/admin` and `/ai/*`. The workflow canvas saves each step's type, position and optional display name, runs the graph against a chosen project, and shows each run's step outcomes on the nodes; approval and retry happen from the run bar. Approved clips are uploaded to YouTube from **Đăng tải / Publishing**.

Every control in the production interface works: features without a backend are hidden rather than marked "coming soon" (Phase 16; see [docs/FINAL_PRODUCT_AUDIT.md](docs/FINAL_PRODUCT_AUDIT.md) for what was implemented and what was hidden). The old single-tool pages under `/ai/*` redirect to **Create**, where the same features are workflow templates.

The interface defaults to Vietnamese and can be switched to English or 日本語 from the account menu, Settings or the sign-in screen. The choice is stored in the `rf_locale` cookie so the server renders the chosen language. Dictionaries live in `frontend/src/lib/i18n/`; `vi.ts` defines the keys, and the type checker rejects an English or Japanese dictionary that misses one. Backend error messages are translated through the same dictionaries. This interface update adds no migration: the API only gained `GET /api/workflow-runs`, `created_at` fields and the user's email on the dashboard, and an optional `label` on workflow graph nodes.

### Updating an existing installation

Back up PostgreSQL, pull the new source, stop the API, and run `python -m alembic upgrade head` from the project root before restarting Uvicorn. The `0002_plans_users` migration retains existing users, studios and content, seeds Trial / Standard / Pro, and creates a subscription for each existing studio. The previous Trial project limit is copied into the new Trial plan. Restart Next.js to load the Admin screen. Do not run `stamp head` to perform this upgrade.

Migration `0003_billing_orders` adds optional VND prices to plans and payment orders. Existing subscriptions and data remain unchanged. Install the updated `requirements.txt` before restarting the API.

Migration `0004_credits_usage` creates an account for every existing studio, an append-only credit ledger and usage records. Existing balances start at zero. An order saves its credit award when checkout starts; old pending orders from before this migration have a zero award. Subsequent migrations add AI tools (`0005`), workflow runs (`0006`), durable jobs and asset lineage (`0007`), login protection (`0008`), encrypted YouTube OAuth connections (`0009`) and publication records (`0010`). Always apply `upgrade head` with the matching source before starting the API or workers.

### Payment gateways (Phase 19)

A system admin configures both gateways in **Quản trị → Thanh toán → Cổng thanh toán**, with no SSH and no restart.

- **VietQR / Bank Transfer (payOS):** Client ID, API Key and Checksum Key.
- **Credit / Debit Card (OnePAY):** Merchant ID, Access Code, Hash Key and QueryDR credentials, in Sandbox or Production mode. Production needs a confirmation.

Secrets are write-only and encrypted at rest with `REELFORGE_TOKEN_ENCRYPTION_KEY`; back that key up with the database. Each gateway can be disabled for new checkouts without affecting existing orders. The configuration methods below still work as a fallback for existing deployments. See [docs/PAYMENTS.md](docs/PAYMENTS.md#configuration-phase-19-admin-managed).

### VNQR checkout with payOS

1. Open a payOS merchant account and create a payment channel. In **Quản trị**, configure the Standard and Pro prices in VND per 30 days. Empty prices keep checkout disabled; Admin → Plans shows whether each plan is purchasable.
2. Enter the keys in **Quản trị → Thanh toán → Cổng thanh toán → VietQR**. Alternatively (the pre-Phase 19 way, still supported), in your ignored `instance/bootstrap.json`, keep `database_url` and add the private keys:

```json
"payos": {"client_id": "YOUR_CLIENT_ID", "api_key": "YOUR_API_KEY", "checksum_key": "YOUR_CHECKSUM_KEY"}
```

Put that `payos` property alongside `database_url` inside the same JSON object. Keep the real keys only on the backend server. Never commit `instance/bootstrap.json`. Configure a public HTTPS endpoint for the backend at `/api/webhooks/payos` in your payOS channel; localhost cannot receive live webhooks. Set `frontend_origin` in System Settings to your public HTTPS frontend URL and enable secure cookies. Verify the callback configuration with payOS before accepting customers.

The workspace owner can select a higher priced plan under **Gói & credits**. The server freezes the VND price in a payment order, requests a payOS hosted link, and updates the subscription for 30 days only after validating the signed webhook and matching its amount. Repeated callbacks do not extend the subscription again. A return to the website is informational, not proof of payment. Admin changes to a subscription remain manual and bypass checkout; account for them separately. Crypto payments are not enabled.

### Card payments with OnePAY

Card payment is a second payment method on the same orders and settlement code (Phase 14). Configure it in **Cổng thanh toán → Thẻ** (or, as a fallback, set `ONEPAY_MERCHANT_ID`, `ONEPAY_ACCESS_CODE`, `ONEPAY_HASH_KEY` and, recommended, `ONEPAY_QUERY_USER` / `ONEPAY_QUERY_PASSWORD` in `.env.runtime`), and register `https://<frontend>/api/webhooks/onepay` (IPN) and `https://<frontend>/api/billing/onepay/return` (return) with OnePAY. The buyer then chooses **VietQR / Bank Transfer** or **Credit / Debit Card** after picking a plan; only enabled, configured methods are offered. Card details are entered on OnePAY's page, never in ReelForge. An order is paid only by OnePAY's signed IPN or a server-side QueryDR check, for its exact amount, and only once. See [docs/PAYMENTS.md](docs/PAYMENTS.md).

The same page now permits a paid plan to be renewed for another 30 days and can ask payOS for the authoritative status of a pending order when the webhook has not yet arrived. The status becomes **expired** when the end date passes and protected operations stop; no background scheduler is needed for this check. Subscription renewal is manual, not an automatic debit. A confirmed payment grants the credits captured on its order once; admin adjustments are recorded in the credit ledger. A supported video job reserves credits before it enters the queue and records usage after a successful MP4 save. A clear rejection before the provider accepts a task refunds the reservation. If submission or a later result is uncertain, the run becomes **needs_attention**, keeps the reserved credits, and cannot be retried; a system admin resolves the held reservation in **Admin → Reconciliation** by confirming consumption or refunding the exact held amount. Decisions are audited and idempotent; see [Credit reconciliation](docs/CREDIT_RECONCILIATION.md). Deploy migration `0011_credit_reconciliation` before restarting the updated API/workers. Failed/canceled payments do not grant credits. Refund processing is not automated: reconcile any refund with the payment provider and the admin before changing an existing paid subscription.

The system administrator can open **Quản trị** to create a user with a new studio, assign a plan, pause a subscription, disable an account, or edit project and workflow limits. The console fits the window: users, studios, payments, reconciliation items and jobs are server-paginated tables with server-side search and filters (`GET /api/admin/users|workspaces|payments?q&…&limit&offset`), and the header counts come from `COUNT` queries (`GET /api/admin`), so it stays fast with many accounts. The sign-in screen also offers self-registration after initial admin setup: each new user receives a separate Trial studio. The system admin can turn registration off in System Settings; it is enabled by default. There is no email verification, password recovery or email delivery yet; admin-created initial passwords must be shared through an appropriate channel. An account disabled by the admin loses its existing login sessions. Admin subscription changes are manual and do not charge anyone. A confirmed payOS payment grants the order's captured `monthly_credits` once; the credit ledger records grants, admin adjustments and usage.

## Settings and data

Application settings are in the database: `system_settings` holds the frontend origin, cookie security, media path and Trial project quota; `workspace_settings` holds each studio's language, video orientation, approval preference, default platform, tone and length for writing steps that leave them empty, and default publishing time; `user_profiles` holds an optional display name. The Settings dashboard edits supported values through authenticated, role-checked endpoints. Uploads themselves remain in the filesystem at `instance/media` by default; PostgreSQL stores their metadata and the storage path setting. Back up the database and media directory together.

The application accepts the supplied `postgresql://` URL and explicitly selects the installed `psycopg` driver. It removes `uselibpqcompat=true` because psycopg/libpq does not recognize that provider compatibility option; `sslmode=require` remains enabled. The example contains no working password. The migration creates the tables in PostgreSQL’s default `public` schema; no separate schema needs to be created. Future model changes should be tracked with Alembic revisions, rather than `create_all`.

The database URL and Next.js-to-API address are deployment bootstrap details and cannot be stored exclusively in PostgreSQL without a separate service-discovery mechanism. payOS keys remain in the private backend bootstrap file. Provider API keys and Google OAuth credentials are read from environment variables; YouTube tokens and resumable upload sessions are encrypted before storage in PostgreSQL with a Fernet key kept outside the database. Back up that key securely alongside the database and media: losing it makes saved connections and upload sessions unreadable.

For remote access, put HTTPS in front of the frontend, set `frontend_origin` and `secure_cookies` in System Settings, keep the API and PostgreSQL private, and apply matching request-body and rate limits at the reverse proxy. Changing the frontend origin may require signing in again on the new address.

For an existing instance using `instance/config.json`, the backend reads it if `instance/bootstrap.json` is absent. If tables were created by an older version without Alembic, back up and verify its schema against the initial migration before stamping `python -m alembic stamp head` (stamping does not create or change tables). On a fresh empty database use `upgrade head`, never `stamp head`. Old `storage_dir`, `secure_cookies` and `frontend_origin` values are imported into the database once if settings rows do not exist. The old file can then be replaced with `instance/bootstrap.json` containing only `database_url`. Existing SQLite data must be migrated to PostgreSQL separately; changing the URL does not migrate data.

## Current architecture and next steps

- Projects, assets, workflows and workspace settings are scoped to the signed-in user's workspace. The first user is the system admin.
- Active subscriptions enforce the configured project and workflow limits on new records. Workspace owners can buy or renew paid plans with VietQR (payOS) or a bank card (OnePAY) when prices and merchant credentials are configured. Confirmed orders update subscriptions and credit balances; admins can make manual subscription and credit adjustments.
- Workflow diagrams support adding, moving, connecting and removing nodes; the saved graph is validated as an acyclic graph and scoped to a workspace. Old linear workflow templates are displayed as graphs without a schema change. Starting a workflow persists ordered step outcomes and a graph snapshot. Local `idea`, `assets` and `scenes` nodes complete; text, `image` and `video` nodes reserve credits and queue jobs; unsupported nodes are blocked, and their dependents are skipped. See the run details below.
- Projects can be opened to edit their topic and preview/download generated MP4 assets. The AI tool catalog is persisted per workspace. The Channels panel connects YouTube; approved runs can queue a private YouTube upload and show its status. Calendar and analytics remain planning views.

### AI tools and workflow readiness

The **Model AI** screen stores task, provider, model, and enabled status per workspace in PostgreSQL. Apply migration `0005_ai_tools` or later with `python -m alembic upgrade head` before restarting the API. A workflow's `GET /api/workflows/{id}/readiness` checks the selected supported model, its provider key, orientation and credit balance, and flags unsupported steps. This check makes no provider request and charges no credits. Selecting a model in the catalog alone does not start a provider job. Other AI task entries currently store configuration only.

### Workflow runs (migration 0006)

After `python -m alembic upgrade head`, choose a project on the workflow screen and run a saved graph. The engine orders nodes by dependencies, records a graph snapshot and step outcomes in PostgreSQL, and displays the most recent 30 runs. The `idea` node reads the selected project's title/topic; `assets` lists media in that studio. A supported `video` node queues work for the separate worker. Render, publish, and other unimplemented graph steps remain blocked with a reason; dependent steps are skipped. A blocked or failed run can be retried using **its original graph snapshot**, producing a new run linked to the old one. Once a clip has been approved, retry is refused even if a later unsupported node left the aggregate run blocked; start a new run to request another video. API routes: `POST/GET /api/workflows/{id}/runs`, `GET /api/workflow-runs/{id}`, and `POST /api/workflow-runs/{id}/retry`.

### One-clip video jobs (migration 0007)

Apply `python -m alembic upgrade head` before starting the API and worker. Fund the workspace balance through a confirmed paid order or an admin credit adjustment. In **Model AI**, enable one of the supported video tools:

| Provider | Model ID | Required environment variable |
| --- | --- | --- |
| fal | `fal-ai/veo3.1/fast` | `FAL_KEY` |
| Runware | `bytedance:seedance@2.5` | `RUNWARE_API_KEY` |
| Replicate | `google/veo-3.1-fast` | `REPLICATE_API_TOKEN` |
| Runway Dev | `gen4.5` | `RUNWAYML_API_SECRET` and `RUNWAY_OUTPUT_HOSTS` |

Set the selected provider key in both the API and video worker environments. Set `VIDEO_CREDITS_PER_CLIP` on the API if the default reservation of 10 credits per clip needs changing. This is an internal flat credit quote, not a live provider price; set it to cover your actual provider cost. The workspace video orientation can be vertical (9:16) or horizontal (16:9); square is not supported by this workflow. The API validates the model's declared capabilities before reserving credits. The standard one-clip flow requests an eight-second 720p video, so a provider/model must support those settings. Runway Gen-4.5 text-to-video produces no generated audio. For Runway, set `RUNWAY_OUTPUT_HOSTS` in the API and worker to the exact comma-separated DNS hostnames approved for its signed MP4 output URLs; its CDN hostname is not fixed by the public API contract, so verify the host for your account before enabling production jobs. The worker refuses output from any other host.

To try the separately operated `dola-render-gateway`, set `DOLA_EXPERIMENTAL_ENABLED=1`, `DOLA_API_KEY` and `DOLA_BASE_URL` in both the API and video worker environments, then select provider `dola` with model `seedance-2.5` or `seedance-2.0`. `DOLA_MEDIA_BASE_URL` can pin a separate operator-controlled media origin. Dola requests use ten seconds and provider-selected resolution. `DOLA_MAX_JOB_AGE_SECONDS` defaults to 7200 (allowed 60–86400) and ends a stuck ReelForge job; inspect the gateway before retrying because its browser task may finish later. The gateway's original `/videos` endpoint can be public, so isolate it appropriately and use the private ReelForge copy for playback. This path has fake-HTTP tests but has not been checked with a live Dola account.

New workflows start with `idea → video → review`. Select a project with a title or topic and start a run; a supplied prompt overrides the project's topic. The API freezes the prompt, model, and credit cost on the queued job. Start the worker in a separate terminal or service from the repository root:

```bash
python -m app.video_worker
```

The worker polls the selected provider, checks MP4 container structure, saves the result as a private workspace asset linked to the project and run, and leaves the run awaiting manual review. This structural check rejects truncated files; it is not a full codec/decode check. Run details poll for progress and expose the saved asset. After reviewing the clip, the workspace owner can use **Duyệt video** in the workflow run bar or call `POST /api/workflow-runs/{id}/approve` to complete the review step and mark the project approved. The worker must remain running to advance queued jobs. `VIDEO_JOB_MAX_AGE_SECONDS` in the worker defaults to 21600 (allowed 60–86400); an over-age job ends, but credits stay reserved when the provider may have accepted it. Inspect the provider and reconcile before requesting another video. Approval alone does not publish a video.

### Private YouTube uploads (migrations 0009–0010)

Enable YouTube Data API v3 and register a Google OAuth client with the YouTube upload scope. Register the exact callback URL as an authorized redirect URI; for local development it can be `http://localhost:3000/youtube/callback`, while production needs HTTPS. Set these variables in the API and YouTube worker environments:

| Variable | Purpose |
| --- | --- |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret |
| `GOOGLE_OAUTH_REDIRECT_URI` | Frontend callback URL registered with Google |
| `REELFORGE_TOKEN_ENCRYPTION_KEY` | Persistent Fernet key for OAuth tokens and resumable upload sessions |

Generate a Fernet key once with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; store it as a secret and reuse the same value after restarts. Install dependencies and migrate to `head`, then run the YouTube worker as a separate service:

```bash
python -m app.youtube_worker
```

The workspace owner connects YouTube in **Kênh**, approves a completed video from the workflow run bar, then chooses **Chuẩn bị đăng** (or **Đăng nội dung** on **Đăng tải**). Since Phase 9 the form also sets tags and visibility (private by default, unlisted or public), and a run with a Render step publishes its final MP4. A fresh OAuth callback must include a refresh token; otherwise it fails without replacing the existing connection. The API creates one publication record and durable upload job for that run/channel. The queued job is tied to that connection; disconnecting and reconnecting requires a new manual action before an upload can proceed. The worker starts a resumable upload, encrypts the session URL, and records the YouTube video ID when the private upload succeeds. Temporary Google failures use bounded retries with backoff. The UI shows queued/uploading/succeeded/error states, without a byte-percentage progress bar. A failed job that never started a resumable upload can be retried manually; an uncertain upload is marked **Cần kiểm tra** and is never sent again automatically. Change visibility in YouTube Studio only after checking the uploaded video. Google project verification, consent and API quota determine whether a real channel can use this flow; repository tests use fake provider/Google responses, not a live channel.

The Facebook Page Reels and TikTok Content Posting HTTP adapters in `app/publishers/` are building blocks only. They have no workspace account connection, publication worker or UI action yet. TikTok's inbox flow requires the creator to finish posting in TikTok and must not be represented as a published post.

### Image steps (Phase 4)

An **Image** step generates images with Runway `gen4_image` (AI tool task **Image**; `RUNWAYML_API_SECRET` and `RUNWAY_OUTPUT_HOSTS`, the same as Runway video). With Scenes connected it makes one image per scene from each scene's visual prompt. Otherwise it makes 1–4 images of the prompt override, the connected text or the project topic. Each image is a separate job with its own reservation of `IMAGE_CREDITS_PER_GENERATION` credits (default 2). Run `python -m app.image_worker`, which checks every file's bytes (PNG, JPEG or WEBP only, at most 20 MB) before storing it as a private asset. Successful images are kept when others fail. Uncertain images are reconciled one by one. See [docs/IMAGE_GENERATION.md](docs/IMAGE_GENERATION.md).

### Multi-scene video (Phase 5)

A Video step with Scenes connected and no prompt override makes **one clip per scene**, each its own job with its own credit reservation (`video-reserve:<step>:scene:<n>`); clips are not joined. A prompt override, or only connected text, still makes one clip (`video-reserve:<step>:single`). A workflow may now contain several Video steps. The step completes, and Review can approve every clip, only when all clips are stored. Each uncertain clip is reconciled on its own. Jobs queued before this change keep their per-run references. See [docs/MULTI_SCENE_VIDEO.md](docs/MULTI_SCENE_VIDEO.md), including the retry limitations.

### Social video workflow and YouTube publishing (Phase 9)

- **Templates:** **Workflows → From a template** creates a **YouTube Short** (9:16, about 50 s) or a **YouTube landscape video** (16:9, about 2 min). Each is the full pipeline: Idea → AI Writer → Scene Splitter → Video + Voice + Subtitle → Render → Review → Publish, plus a Metadata step. Templates name no model; each step uses the first enabled model for its task unless its settings choose one.
- **Running:** the Run dialog shows the project topic and models. The run bar's **Summary** shows:
  - steps and active jobs;
  - what was made;
  - the final video;
  - the run's credits (reserved, used, refunded, held);
  - failed steps and steps that need reconciliation.

  **Download final MP4** serves the render through the asset endpoint.
- **Publishing:** after **Approve video**, the Publish step hands the final render and the prepared metadata to **Prepare publishing**:
  - the form holds title, description, tags and visibility (private, unlisted or public), checked against YouTube's limits;
  - nothing is uploaded until you press Publish;
  - uploads use the existing durable YouTube worker;
  - a failed upload that sent no media can be retried, with corrected metadata, without generating anything again.

  Migration `0013_publication_metadata` adds visibility and tags. See [docs/SOCIAL_VIDEO_WORKFLOW.md](docs/SOCIAL_VIDEO_WORKFLOW.md).

### Content sources, repurposing and Movie Recap (Phases 10–11)

- **Sources:** Text Source, URL Source, Uploaded Media Source and Transcript steps bring existing content into a workflow.
  - Uploads accept TXT, MD, SRT and VTT documents.
  - URL Source reads one public `https://` page safely: it refuses private, local and metadata addresses, follows no JavaScript and bypasses no paywall.
  - Transcript turns audio or video into timestamped text with the workspace's Transcription model (OpenAI Whisper), as a durable paid job in `python -m app.source_worker`. Subtitle files pass through for free.

  See [docs/CONTENT_SOURCES.md](docs/CONTENT_SOURCES.md).
- **Repurpose Existing Content** template: a page or document becomes a short video. See [docs/REPURPOSING.md](docs/REPURPOSING.md).
- **Movie Recap / Review** template, for content you are authorized to use:
  1. Transcript, then Story Analysis and Recap Script;
  2. Match Source Scenes finds each scene's moment in the uploaded video, locally;
  3. Extract Source Clips cuts it with FFmpeg (stream copy first, H.264/AAC otherwise) and records the source on each clip asset;
  4. Render joins the clips with the narration.

  See [docs/MOVIE_RECAP.md](docs/MOVIE_RECAP.md).

### TikTok, Facebook and scheduling (Phases 12–13)

- **Channels:** YouTube, TikTok (inbox drafts via the Content Posting API) and Facebook Page Reels, each through its official OAuth and upload API. Tokens are encrypted, and each channel shows connected, not connected, configuration required or authorization required.
- **Publishing:** the publish dialog posts one approved video to several channels at once. Each channel has its own metadata, publication and independent upload job (`python -m app.social_worker` for TikTok and Facebook).
- **Scheduling:** a publication can be scheduled (UTC), moved or cancelled until its upload starts; `python -m app.scheduler_worker` queues it on time. The Calendar shows every channel.
- Migration `0014_channels_scheduling_ops`. See [docs/MULTI_PLATFORM_PUBLISHING.md](docs/MULTI_PLATFORM_PUBLISHING.md) and [docs/SCHEDULING.md](docs/SCHEDULING.md).
- **Operations:**
  - default models per task;
  - worker heartbeats and an admin job view with a stuck-work audit;
  - storage per workspace;
  - cleanup of worker temp folders and orphan files.

  See [docs/OPERATIONS.md](docs/OPERATIONS.md).

### Payments, admin console and product completion (Phases 14–16)

- **Payments:** VietQR (payOS) and bank cards (OnePAY) share one order table and one settlement path that checks the provider and the exact amount and pays an order once. Billing history is paginated. See [docs/PAYMENTS.md](docs/PAYMENTS.md).
- **Admin console:** fits the window; Users, Studios & credits, Plans, Payments, Credit reconciliation and Operations are server-paginated tables with dialogs for each action. Accounts are created from a dialog on the Users tab.
- **Product completion:** no "coming soon" control remains.
  - Real now: Background Music (an uploaded track mixed under the narration), image slideshows in Render, the Movie Review, Article → Video and Product Video templates, the Library's Scripts tab, "Add to project" for uploads, a display name, workspace defaults for platform, tone, length and publishing time, and a paginated publishing queue.
  - Hidden: Instagram, voice cloning, sound effects, the audio mixer, timeline editing and other advanced steps.

  See [docs/FINAL_PRODUCT_AUDIT.md](docs/FINAL_PRODUCT_AUDIT.md).
- Migration `0015_admin_payments_profiles` (user display names and admin/payment indexes).

### Storage lifecycle (Phase 17)

- **One media root**: `REELFORGE_STORAGE_ROOT` (on the home server `/srv/data/videos/reelforge`, on the HDD), with files named by workspace and asset IDs only.
- **Plan quotas**: Trial 1 GB, Standard 10 GB and Pro 30 GB by default, edited in Admin → Plans. Warnings appear at 70, 80, 90 and 100 %, and a full studio cannot store anything new. Checks take the studio's lock, so concurrent writes cannot overshoot.
- **Retention by kind**: scene videos, narration, generated images and extracted clips of runs that have their final video expire after 30 days. Final videos and uploads are never removed automatically. Run the daily job at 03:00 with `python -m app.media_maintenance --apply --intermediates`; it is a dry run without `--apply`.
- **User cleanup**: owners delete media on the Media page, or a project's intermediate media in Settings → Storage, after a confirmation.
- Migration `0016_storage_lifecycle`. See [docs/STORAGE.md](docs/STORAGE.md).

### Notifications, support and live verification (Phase 18)

- **Notification bell** with live updates (Server-Sent Events, polling as a fallback) for runs, publishing, payments, credits, storage and support. Behind nginx, turn buffering off for `/api/notifications/stream`; Cloudflare Tunnel needs nothing. See [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md).
- **Support**: account menu → Hỗ trợ for users; Admin → Hỗ trợ for system admins. See [docs/SUPPORT.md](docs/SUPPORT.md).
- **Payment gateways** (system admins): see *Payment gateways (Phase 19)* above.
- **Admin → Kiểm định**: safe readiness checks and the manual live-verification checklist. See [docs/LIVE_VERIFICATION.md](docs/LIVE_VERIFICATION.md).
- Migration `0017_notify_support_verify`.
- Phase 19: admin-managed payment gateways; migration `0018_admin_payment_config`.

### Voice, subtitles and the final render (Phases 6–8)

- **Voice** reads a script as one narration, or each scene's text as its own narration, with Google Gemini TTS (AI tool task **Voice**, `GEMINI_API_KEY`). Each narration is a job with its own reservation of `VOICE_CREDITS_PER_GENERATION` credits (default 1), and is stored as a checked WAV file by `python -m app.voice_worker`. See [docs/VOICE_GENERATION.md](docs/VOICE_GENERATION.md).
- **Subtitle** writes an SRT or WebVTT file on the server, for free and with no job. It is timed by the narration, then the clips, then the scene estimates. See [docs/SUBTITLES.md](docs/SUBTITLES.md).
- **Render** joins the scene clips in order, adds the narration (which mutes the clips' own audio) and burns in the subtitles with system FFmpeg, producing one H.264/AAC MP4. `python -m app.render_worker` runs FFmpeg outside any request. Rendering is free by default (`RENDER_CREDITS_PER_JOB`), and failures are refunded, never reconciled. Review previews the final video, and publishing prefers it. Install FFmpeg and Noto fonts first. See [docs/RENDERING.md](docs/RENDERING.md).

### Text nodes

AI Writer, Summarize, Rewrite, Translate, Hook, Title and CTA steps generate text with the Text model chosen in the step's settings, or else the first enabled **Text** model (AI tool task `script`) whose provider is `openai`, `anthropic` or `gemini`. Keys come from `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` in the shared runtime environment and are never stored in PostgreSQL. A text step holds `TEXT_CREDITS_PER_GENERATION` credits (default 1) when it is queued, charges them as one usage event when the text arrives, and refunds deterministic rejections. `python -m app.text_worker` calls the provider outside any database transaction and retries rate-limit rejections up to three times. Ambiguous outcomes (including timeouts, a worker interrupted mid-call, or empty/content-filtered output that may have incurred a charge) require [reconciliation](docs/CREDIT_RECONCILIATION.md). Successful steps let the next steps run. A node's optional `config` (prompt, language, tone, platform, duration, target language, count…) is saved with the workflow graph and validated per node type (see Node settings).

### Typed connections

Each node type declares typed input and output ports (`app/workflow/ports.py`). `GET /api/workflow-node-types` serves them to the canvas, which draws one labeled handle per port and only accepts compatible connections. An edge stores the ports it connects as `sourceHandle`/`targetHandle`. For example, the AI Writer's `script` feeds the scene splitter's `script`, and its `scenes` feed the video node's `scenes`. Edges saved before ports existed connect the source's first output to the first compatible input, and are rewritten with their ports when read, saved or run; no migration is needed. Before a node runs, its inputs are resolved from connected edges, then its `config`, then the project topic. A node missing a required input is blocked with a reason instead of running. The scene splitter (`scenes`) runs locally and costs no credits.

### Node settings

Select a step on the canvas to edit its settings in the right-hand inspector: language, tone, platform, target duration, brief and instructions for AI Writer; length, style or count for the other text steps; target scene duration, maximum scenes and visual style for the scene splitter; and model, aspect ratio (9:16 or 16:9, or the workspace setting), clip length (4, 6 or 8 seconds, or the model default) and an optional prompt override for the video step. Each text or video step can pick one of the workspace's enabled models; API keys never reach the browser. The fields come from the same schema the API validates (`config` in `GET /api/workflow-node-types`, defined by each handler's `config_fields`). Changes mark the workflow unsaved, support undo and redo, and are stored by **Save**; a setting left at its default is not stored. Saving invalid settings returns 422 with a stable code such as `invalid_language`, `invalid_duration`, `invalid_scene_limit` or `unsupported_model`. Readiness reports invalid settings, a disabled model and missing required inputs before a run. A run freezes the settings in its graph snapshot, so later edits change neither its history nor a retry of it. The Run dialog now only picks the project; `prompt` and `tool_id` in `POST /api/workflows/{id}/runs` still work for API clients, but a step's own settings take precedence.

### Worker and storage settings

Run `python -m app.video_worker`, `python -m app.text_worker`, `python -m app.image_worker`, `python -m app.voice_worker`, `python -m app.render_worker`, `python -m app.source_worker`, `python -m app.youtube_worker`, `python -m app.social_worker` and `python -m app.scheduler_worker` continuously as separate processes (only the ones you use). Each reports a heartbeat that **Admin → Operations** shows. All support `--once` for one due job. Each plan sets a studio's media limit (Phase 17); `WORKSPACE_MEDIA_QUOTA_BYTES`, when set, caps every studio for the API and all workers; individual uploads are capped at 100 MiB and checked against their media signature. Upload requests are authenticated before the API reads their bodies, while the reverse proxy still needs body, rate and concurrency limits. Keep the API, workers and media directory on storage they can all access. Give the API and every worker the same provider keys through `.env.runtime` or one `EnvironmentFile=` (see Provider keys, processes and logs). Back up PostgreSQL and `instance/media` together. API keys stay server-side; do not put them in the Next.js frontend or Git.

Storage quotas, retention and the daily cleanup are described in [docs/STORAGE.md](docs/STORAGE.md). If a worker crashes, preview abandoned temporary files with `python -m app.media_maintenance`:

- `.part` downloads;
- `.render-tmp`, `.source-tmp` and `.publish-tmp` leftovers;
- with `--orphans`, files that have no asset row.

Run it with `--apply` to remove eligible files older than 24 hours. The command restricts cleanup to known workspace directories, never follows links, and is a dry run unless `--apply` is supplied. `--usage` prints the storage used per workspace. See [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Development checks

Install test dependencies into the same Python environment as the API, then run the backend tests:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

From `frontend/`, run `npm ci`, `npm run typecheck`, and `npm run build`. GitHub Actions runs these Python and frontend checks on pushes and pull requests. The suite never calls a paid provider: `tests/test_live_providers.py` is skipped unless `REELFORGE_LIVE_TESTS=1` is set in the shell.
