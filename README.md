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

## Settings and data

Application settings are in the database: `system_settings` holds the frontend origin, cookie security, media path and Trial project quota; `workspace_settings` holds each studio's language, video orientation and approval preference. The Settings dashboard edits supported values through authenticated, role-checked endpoints. Uploads themselves remain in the filesystem at `instance/media` by default; PostgreSQL stores their metadata and the storage path setting. Back up the database and media directory together.

The application accepts the supplied `postgresql://` URL and explicitly selects the installed `psycopg` driver. It removes `uselibpqcompat=true` because psycopg/libpq does not recognize that provider compatibility option; `sslmode=require` remains enabled. The example contains no working password. The migration creates the tables in PostgreSQL’s default `public` schema; no separate schema needs to be created. Future model changes should be tracked with Alembic revisions, rather than `create_all`.

The database URL and Next.js-to-API address are deployment bootstrap details and cannot be stored exclusively in PostgreSQL without a separate service-discovery mechanism. Provider API keys are not accepted or stored yet. When those integrations are added, credentials must be encrypted before storage, with the encryption key kept outside the database.

For remote access, put HTTPS in front of the frontend, set `frontend_origin` and `secure_cookies` in System Settings, and keep the API and PostgreSQL private. Changing the frontend origin may require signing in again on the new address.

For an existing instance using `instance/config.json`, the backend reads it if `instance/bootstrap.json` is absent. If tables were created by an older version without Alembic, back up and verify its schema against the initial migration before stamping `python -m alembic stamp head` (stamping does not create or change tables). On a fresh empty database use `upgrade head`, never `stamp head`. Old `storage_dir`, `secure_cookies` and `frontend_origin` values are imported into the database once if settings rows do not exist. The old file can then be replaced with `instance/bootstrap.json` containing only `database_url`. Existing SQLite data must be migrated to PostgreSQL separately; changing the URL does not migrate data.

## Current architecture and next steps

- Projects, assets, workflows and workspace settings are scoped to the signed-in user's workspace. The first user is the system admin.
- Trial's project limit is enforced server-side from a database setting. There is no checkout, plan upgrade endpoint or open registration yet.
- Workflow diagrams support adding, moving, connecting and removing nodes; the saved graph is validated as an acyclic graph and scoped to a workspace. Old linear workflow templates are displayed as graphs without a schema change. These are **designs only**; no graph execution, AI calls, render, review, publishing or usage/billing are implemented.
- The dashboard includes clearly marked planning views for AI tools, channels, scheduling and analytics. They do not accept credentials or publish content yet.
