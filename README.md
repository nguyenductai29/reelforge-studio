# ReelForge Studio

Self-hosted foundation for a short-video production platform. Next.js/React/TypeScript powers the dashboard, while FastAPI/Python serves the API. The implemented path is project topic → one AI-generated MP4 → manual review → private YouTube upload. Video jobs support fal, Runware, Replicate and direct Runway Dev, plus an opt-in experimental Dola gateway; PostgreSQL stores queue leases, credits, asset lineage and per-channel publication state. Facebook/TikTok upload adapters exist, but account connection, publication jobs and scheduling for those channels are not wired into the app. Multi-scene rendering and analytics are still planned. External provider and social-account calls still need validation with real credentials.

## Start locally with PostgreSQL

Requires Python 3.11+, Node.js 20.9+ and an existing PostgreSQL database. Create a dedicated database and user on your PostgreSQL server. Give the user permission to create tables in its own database/schema.

1. Create the local `instance` directory and copy `config.example.json` to `instance/bootstrap.json`. The template contains the requested host, user, database and query string. Replace the literal `PASSWORD` with the **actual password on your server**; it is only a placeholder in the repository. URL-encode the password if it contains reserved URL characters. This is the **only backend bootstrap value outside PostgreSQL**: the app cannot discover a database connection by reading that database. Keep the file out of Git and restrict access to the service account. Do not create a `.env` file.
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

Open http://localhost:3000 to create the first admin account, or run `npm run create-admin` in `frontend` while the API is running (it prompts for the email and password, or reads `ADMIN_EMAIL` and `ADMIN_PASSWORD`). On a public server, create the admin this way before the site is reachable; until an account exists, the first visitor can claim it. API documentation is at http://127.0.0.1:8000/docs. The Next.js proxy defaults to `http://127.0.0.1:8000`; if the API is at a different server address, copy `frontend/config.example.json` to `frontend/instance/config.json` and set `api_base_url` to the address **reachable by the Next.js server**. That address is the frontend's connection bootstrap, not an application preference.

### Studio interface

The dashboard follows the ReelForge Studio design: a dark, cool near-black theme with one cyan-blue accent, built with Tailwind CSS v4 and shadcn/ui (Radix) components under `frontend/src/components/ui`. The theme tokens live in `frontend/src/app/globals.css`. Space Grotesk sets headings and DM Sans sets interface copy; DM Sans has no Vietnamese glyphs, so the Vietnamese interface uses Be Vietnam Pro, which also backs up Vietnamese titles in other languages. All three are self-hosted npm packages (`@fontsource*`), so builds need no font download.

Each module has its own URL: `/` (home), `/create`, `/projects`, `/projects/<id>`, `/workspace/<id>`, `/workflows`, `/workflows/<id>`, `/library`, `/media`, `/publishing`, `/calendar`, `/models`, `/channels`, `/billing`, `/settings`, `/admin` and `/ai/*`. The workflow canvas saves each step's type, position and optional display name, runs the graph against a chosen project, and shows each run's step outcomes on the nodes; approval and retry happen from the run bar. Approved clips are uploaded to YouTube from **Đăng tải / Publishing**.

Screens whose backend does not exist yet — AI Writer, image generation, repurposing, movie recap, scheduling, the AI assistant and the non-YouTube channels — keep their layout but are marked **Sắp có** (coming soon) and never show sample data. Workflow steps without an executor are listed the same way in the step library.

The interface defaults to Vietnamese and can be switched to English or 日本語 from the account menu, Settings or the sign-in screen. The choice is stored in the `rf_locale` cookie so the server renders the chosen language. Dictionaries live in `frontend/src/lib/i18n/`; `vi.ts` defines the keys, and the type checker rejects an English or Japanese dictionary that misses one. Backend error messages are translated through the same dictionaries. This interface update adds no migration: the API only gained `GET /api/workflow-runs`, `created_at` fields and the user's email on the dashboard, and an optional `label` on workflow graph nodes.

### Updating an existing installation

Back up PostgreSQL, pull the new source, stop the API, and run `python -m alembic upgrade head` from the project root before restarting Uvicorn. The `0002_plans_users` migration retains existing users, studios and content, seeds Trial / Standard / Pro, and creates a subscription for each existing studio. The previous Trial project limit is copied into the new Trial plan. Restart Next.js to load the Admin screen. Do not run `stamp head` to perform this upgrade.

Migration `0003_billing_orders` adds optional VND prices to plans and payment orders. Existing subscriptions and data remain unchanged. Install the updated `requirements.txt` before restarting the API.

Migration `0004_credits_usage` creates an account for every existing studio, an append-only credit ledger and usage records. Existing balances start at zero. An order saves its credit award when checkout starts; old pending orders from before this migration have a zero award. Subsequent migrations add AI tools (`0005`), workflow runs (`0006`), durable jobs and asset lineage (`0007`), login protection (`0008`), encrypted YouTube OAuth connections (`0009`) and publication records (`0010`). Always apply `upgrade head` with the matching source before starting the API or workers.

### VNQR checkout with payOS

1. Open a payOS merchant account and create a payment channel. In **Quản trị**, configure the Standard and Pro prices in VND per 30 days. Empty prices keep checkout disabled.
2. In your ignored `instance/bootstrap.json`, keep `database_url` and add the private keys:

```json
"payos": {"client_id": "YOUR_CLIENT_ID", "api_key": "YOUR_API_KEY", "checksum_key": "YOUR_CHECKSUM_KEY"}
```

Put that `payos` property alongside `database_url` inside the same JSON object. Keep the real keys only on the backend server. Never commit `instance/bootstrap.json`. Configure a public HTTPS endpoint for the backend at `/api/webhooks/payos` in your payOS channel; localhost cannot receive live webhooks. Set `frontend_origin` in System Settings to your public HTTPS frontend URL and enable secure cookies. Verify the callback configuration with payOS before accepting customers.

The workspace owner can select a higher priced plan under **Gói & credits**. The server freezes the VND price in a payment order, requests a payOS hosted link, and updates the subscription for 30 days only after validating the signed webhook and matching its amount. Repeated callbacks do not extend the subscription again. A return to the website is informational, not proof of payment. Admin changes to a subscription remain manual and bypass checkout; account for them separately. Card checkout requires actual OnePAY merchant integration details; it is not active. Crypto payments are not enabled.

The same page now permits a paid plan to be renewed for another 30 days and can ask payOS for the authoritative status of a pending order when the webhook has not yet arrived. The status becomes **expired** when the end date passes and protected operations stop; no background scheduler is needed for this check. Subscription renewal is manual, not an automatic debit. A confirmed payment grants the credits captured on its order once; admin adjustments are recorded in the credit ledger. A supported video job reserves credits before it enters the queue and records usage after a successful MP4 save. A clear rejection before the provider accepts a task refunds the reservation. If submission or a later result is uncertain, the run becomes **needs_attention**, keeps the reserved credits, and cannot be retried; an operator must reconcile provider charges and use the existing admin credit adjustment if a refund is warranted. Failed/canceled payments do not grant credits. Refund processing is not automated: reconcile any refund with the payment provider and the admin before changing an existing paid subscription.

The system administrator can open **Quản trị** to create a user with a new studio, assign a plan, pause a subscription, disable an account, or edit project and workflow limits. The sign-in screen also offers self-registration after initial admin setup: each new user receives a separate Trial studio. The system admin can turn registration off in System Settings; it is enabled by default. There is no email verification, password recovery or email delivery yet; admin-created initial passwords must be shared through an appropriate channel. An account disabled by the admin loses its existing login sessions. Admin subscription changes are manual and do not charge anyone. A confirmed payOS payment grants the order's captured `monthly_credits` once; the credit ledger records grants, admin adjustments and usage.

## Settings and data

Application settings are in the database: `system_settings` holds the frontend origin, cookie security, media path and Trial project quota; `workspace_settings` holds each studio's language, video orientation and approval preference. The Settings dashboard edits supported values through authenticated, role-checked endpoints. Uploads themselves remain in the filesystem at `instance/media` by default; PostgreSQL stores their metadata and the storage path setting. Back up the database and media directory together.

The application accepts the supplied `postgresql://` URL and explicitly selects the installed `psycopg` driver. It removes `uselibpqcompat=true` because psycopg/libpq does not recognize that provider compatibility option; `sslmode=require` remains enabled. The example contains no working password. The migration creates the tables in PostgreSQL’s default `public` schema; no separate schema needs to be created. Future model changes should be tracked with Alembic revisions, rather than `create_all`.

The database URL and Next.js-to-API address are deployment bootstrap details and cannot be stored exclusively in PostgreSQL without a separate service-discovery mechanism. payOS keys remain in the private backend bootstrap file. Provider API keys and Google OAuth credentials are read from environment variables; YouTube tokens and resumable upload sessions are encrypted before storage in PostgreSQL with a Fernet key kept outside the database. Back up that key securely alongside the database and media: losing it makes saved connections and upload sessions unreadable.

For remote access, put HTTPS in front of the frontend, set `frontend_origin` and `secure_cookies` in System Settings, keep the API and PostgreSQL private, and apply matching request-body and rate limits at the reverse proxy. Changing the frontend origin may require signing in again on the new address.

For an existing instance using `instance/config.json`, the backend reads it if `instance/bootstrap.json` is absent. If tables were created by an older version without Alembic, back up and verify its schema against the initial migration before stamping `python -m alembic stamp head` (stamping does not create or change tables). On a fresh empty database use `upgrade head`, never `stamp head`. Old `storage_dir`, `secure_cookies` and `frontend_origin` values are imported into the database once if settings rows do not exist. The old file can then be replaced with `instance/bootstrap.json` containing only `database_url`. Existing SQLite data must be migrated to PostgreSQL separately; changing the URL does not migrate data.

## Current architecture and next steps

- Projects, assets, workflows and workspace settings are scoped to the signed-in user's workspace. The first user is the system admin.
- Active subscriptions enforce the configured project and workflow limits on new records. Workspace owners can buy or renew paid plans through payOS when prices and merchant credentials are configured. Confirmed orders update subscriptions and credit balances; admins can make manual subscription and credit adjustments.
- Workflow diagrams support adding, moving, connecting and removing nodes; the saved graph is validated as an acyclic graph and scoped to a workspace. Old linear workflow templates are displayed as graphs without a schema change. Starting a workflow persists ordered step outcomes and a graph snapshot. Local `idea` and `assets` nodes complete; one configured supported `video` node queues a job; unsupported nodes are blocked, and their dependents are skipped. See the run details below.
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

The workspace owner connects YouTube in **Kênh**, approves a completed clip from the workflow run bar, then chooses **Đăng nội dung → Tải riêng tư lên YouTube** on **Đăng tải**. A fresh OAuth callback must include a refresh token; otherwise it fails without replacing the existing connection. The API creates one publication record and durable upload job for that run/channel. The queued job is tied to that connection; disconnecting and reconnecting requires a new manual action before an upload can proceed. The worker starts a resumable upload, encrypts the session URL, and records the YouTube video ID when the private upload succeeds. Temporary Google failures use bounded retries with backoff. The UI shows queued/uploading/succeeded/error states, without a byte-percentage progress bar. A failed job that never started a resumable upload can be retried manually; an uncertain upload is marked **Cần kiểm tra** and is never sent again automatically. Change visibility in YouTube Studio only after checking the uploaded video. Google project verification, consent and API quota determine whether a real channel can use this flow; repository tests use fake provider/Google responses, not a live channel.

The Facebook Page Reels and TikTok Content Posting HTTP adapters in `app/publishers/` are building blocks only. They have no workspace account connection, publication worker or UI action yet. TikTok's inbox flow requires the creator to finish posting in TikTok and must not be represented as a published post.

### Text nodes

AI Writer, Summarize, Rewrite, Translate, Hook, Title and CTA steps generate text with the first enabled **Text** model (AI tool task `script`) whose provider is `openai`, `anthropic` or `gemini`. Keys come from `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` in the API and text worker environments and are never stored in PostgreSQL. A text step holds `TEXT_CREDITS_PER_GENERATION` credits (default 1) when it is queued, charges them as one usage event when the text arrives, and refunds them if generation fails. `python -m app.text_worker` calls the provider outside any database transaction, retries transient failures up to three times, and then lets the next steps run. A node's optional `config` (prompt, language, tone, platform, duration, target language, count…) is saved with the workflow graph and validated per node type.

### Worker and storage settings

Run `python -m app.video_worker`, `python -m app.text_worker` and `python -m app.youtube_worker` continuously as separate processes. All support `--once` for one due job. `WORKSPACE_MEDIA_QUOTA_BYTES` sets the per-workspace media ceiling (default 1 GiB) for the API and video worker; individual uploads are capped at 100 MiB and checked against their media signature. Upload requests are authenticated before the API reads their bodies, while the reverse proxy still needs body, rate and concurrency limits. Keep the API, workers and media directory on storage they can all access. Back up PostgreSQL and `instance/media` together. API keys stay server-side; do not put them in the Next.js frontend or Git.

If a worker crashes during download, preview abandoned temporary files with `python -m app.media_maintenance`. Run `python -m app.media_maintenance --apply` to remove eligible `.part` files older than 24 hours. The command restricts cleanup to known workspace directories and is a dry run unless `--apply` is supplied.

## Development checks

Install test dependencies into the same Python environment as the API, then run the backend tests:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

From `frontend/`, run `npm ci`, `npm run typecheck`, and `npm run build`. GitHub Actions runs these Python and frontend checks on pushes and pull requests.
