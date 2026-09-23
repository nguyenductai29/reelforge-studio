# ReelForge Studio

Self-hosted foundation for a short-video production platform. Next.js/React/TypeScript powers the dashboard, while FastAPI/Python serves the API. Current features: first-run admin setup, login, workspace projects and media, an interactive workflow diagram editor, and database-backed settings. Video generation, rendering, publishing, paid billing and automated workflows are **not yet implemented**.

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

Open http://localhost:3000 to create the first admin account. API documentation is at http://127.0.0.1:8000/docs. The Next.js proxy defaults to `http://127.0.0.1:8000`; if the API is at a different server address, copy `frontend/config.example.json` to `frontend/instance/config.json` and set `api_base_url` to the address **reachable by the Next.js server**. That address is the frontend's connection bootstrap, not an application preference.

### Broadcast Control Room

The Cinematic Noir theme uses charcoal surfaces, warm ivory text, and restrained red accents. Bebas Neue supplies the English display typography; Barlow handles interface copy and Vietnamese titles. Both families are self-hosted, with font provenance and licenses in `frontend/src/app/fonts/`.

The overview uses a responsive production console: studio media on the left, a central 9:16 preview, and saved workflows on the right. Search or filter workspace media, then select an image, video, or audio file to preview its actual contents. The preview offers native playback, contain/fill framing, a reference safe-area overlay, and fullscreen where supported. These viewing controls do not edit the source file or render a project. Workflow selection opens the existing editor and run history.

Every signed-in module stays inside the viewport: navigation, headings, tabs, and primary form actions remain visible while long lists and form fields scroll within their own panels. On smaller screens, Control Room switches between Preview, Media, and Workflow; project/library/AI/settings panels use tabs. Workflow editing, runs, and creation have separate views, as do administration and billing. Tabs keep their panels mounted to preserve unfinished forms and graph edits. The 9:16 preview sizes itself from the space available in its panel. Empty studios show setup actions and actual zero counts. This interface update needs no new migration.

### Updating an existing installation

Back up PostgreSQL, pull the new source, stop the API, and run `python -m alembic upgrade head` from the project root before restarting Uvicorn. The `0002_plans_users` migration retains existing users, studios and content, seeds Trial / Standard / Pro, and creates a subscription for each existing studio. The previous Trial project limit is copied into the new Trial plan. Restart Next.js to load the Admin screen. Do not run `stamp head` to perform this upgrade.

Migration `0003_billing_orders` adds optional VND prices to plans and payment orders. Existing subscriptions and data remain unchanged. Install the updated `requirements.txt` before restarting the API.

Migration `0004_credits_usage` creates an account for every existing studio, an append-only credit ledger and usage records. Existing balances start at zero. An order saves its credit award when checkout starts; old pending orders from before this migration have a zero award.

### VNQR checkout with payOS

1. Open a payOS merchant account and create a payment channel. In **Quản trị**, configure the Standard and Pro prices in VND per 30 days. Empty prices keep checkout disabled.
2. In your ignored `instance/bootstrap.json`, keep `database_url` and add the private keys:

```json
"payos": {"client_id": "YOUR_CLIENT_ID", "api_key": "YOUR_API_KEY", "checksum_key": "YOUR_CHECKSUM_KEY"}
```

Put that `payos` property alongside `database_url` inside the same JSON object. Keep the real keys only on the backend server. Never commit `instance/bootstrap.json`. Configure a public HTTPS endpoint for the backend at `/api/webhooks/payos` in your payOS channel; localhost cannot receive live webhooks. Set `frontend_origin` in System Settings to your public HTTPS frontend URL and enable secure cookies. Verify the callback configuration with payOS before accepting customers.

The workspace owner can select a higher priced plan under **Gói & thanh toán**. The server freezes the VND price in a payment order, requests a payOS hosted link, and updates the subscription for 30 days only after validating the signed webhook and matching its amount. Repeated callbacks do not extend the subscription again. A return to the website is informational, not proof of payment. Admin changes to a subscription remain manual and bypass checkout; account for them separately. Card checkout requires actual OnePAY merchant integration details; it is not active. Crypto payments are not enabled.

The same page now permits a paid plan to be renewed for another 30 days and can ask payOS for the authoritative status of a pending order when the webhook has not yet arrived. The status becomes **expired** when the end date passes and protected operations stop; no background scheduler is needed for this check. Subscription renewal is manual, not an automatic debit. A confirmed payment grants the credits captured on its order once; admin adjustments are recorded in the credit ledger. The `app.usage.consume` service atomically records future AI/render usage and rejects costs above the available balance, but there is no AI provider or render job wired to it yet. Failed/canceled payments do not grant credits. Refund processing is not automated: reconcile any refund with the payment provider and the admin before changing an existing paid subscription.

The system administrator can open **Quản trị** to create a user with a new studio, assign a plan, pause a subscription, disable an account, or edit project and workflow limits. The sign-in screen also offers self-registration after initial admin setup: each new user receives a separate Trial studio. The system admin can turn registration off in System Settings; it is enabled by default. There is no email verification, password recovery or email delivery yet; admin-created initial passwords must be shared through an appropriate channel. An account disabled by the admin loses its existing login sessions. Subscription changes are manual and do not charge anyone. `monthly_credits` is configuration for a future usage engine: credits are not issued, spent or billed yet.

## Settings and data

Application settings are in the database: `system_settings` holds the frontend origin, cookie security, media path and Trial project quota; `workspace_settings` holds each studio's language, video orientation and approval preference. The Settings dashboard edits supported values through authenticated, role-checked endpoints. Uploads themselves remain in the filesystem at `instance/media` by default; PostgreSQL stores their metadata and the storage path setting. Back up the database and media directory together.

The application accepts the supplied `postgresql://` URL and explicitly selects the installed `psycopg` driver. It removes `uselibpqcompat=true` because psycopg/libpq does not recognize that provider compatibility option; `sslmode=require` remains enabled. The example contains no working password. The migration creates the tables in PostgreSQL’s default `public` schema; no separate schema needs to be created. Future model changes should be tracked with Alembic revisions, rather than `create_all`.

The database URL and Next.js-to-API address are deployment bootstrap details and cannot be stored exclusively in PostgreSQL without a separate service-discovery mechanism. Provider API keys are not accepted or stored yet. When those integrations are added, credentials must be encrypted before storage, with the encryption key kept outside the database.

For remote access, put HTTPS in front of the frontend, set `frontend_origin` and `secure_cookies` in System Settings, and keep the API and PostgreSQL private. Changing the frontend origin may require signing in again on the new address.

For an existing instance using `instance/config.json`, the backend reads it if `instance/bootstrap.json` is absent. If tables were created by an older version without Alembic, back up and verify its schema against the initial migration before stamping `python -m alembic stamp head` (stamping does not create or change tables). On a fresh empty database use `upgrade head`, never `stamp head`. Old `storage_dir`, `secure_cookies` and `frontend_origin` values are imported into the database once if settings rows do not exist. The old file can then be replaced with `instance/bootstrap.json` containing only `database_url`. Existing SQLite data must be migrated to PostgreSQL separately; changing the URL does not migrate data.

## Current architecture and next steps

- Projects, assets, workflows and workspace settings are scoped to the signed-in user's workspace. The first user is the system admin.
- Active subscriptions enforce the configured project and workflow limits on new records. Plan administration is manual; there is no checkout or credit ledger yet.
- Workflow diagrams support adding, moving, connecting and removing nodes; the saved graph is validated as an acyclic graph and scoped to a workspace. Old linear workflow templates are displayed as graphs without a schema change. These are **designs only**; no graph execution, AI calls, render, review, publishing or usage/billing are implemented.
- The dashboard includes clearly marked planning views for AI tools, channels, scheduling and analytics. They do not accept credentials or publish content yet.

### AI tools and workflow readiness

The **Công cụ AI** screen stores task, provider, model, and enabled status per workspace in PostgreSQL. Apply migration `0005_ai_tools` with `python -m alembic upgrade head` before restarting the API. A workflow's `GET /api/workflows/{id}/readiness` reports missing AI choices and provider connections without making provider requests or charging credits. Provider credentials and AI generation are not implemented yet; selecting a model does not enable generation. Modules fit the viewport, with independent scrolling inside data and form panels.

### Workflow runs (migration 0006)

After `python -m alembic upgrade head`, choose a project on the workflow screen and run a saved graph. The engine orders nodes by dependencies, records a graph snapshot and step outcomes in PostgreSQL, and displays the most recent 30 runs. The `idea` node reads the selected project's title/topic; `assets` lists media in that studio. AI, render, publish, and other unimplemented steps remain blocked with a reason; dependent steps are skipped. A blocked run can be retried using **its original graph snapshot**, producing a new run linked to the old one. No provider call or credit charge occurs in this phase. API routes: `POST/GET /api/workflows/{id}/runs`, `GET /api/workflow-runs/{id}`, and `POST /api/workflow-runs/{id}/retry`.
