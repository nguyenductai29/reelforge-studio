# ReelForge Studio

Self-hosted foundation for a short-video production platform. Next.js/React/TypeScript powers the dashboard, while FastAPI/Python serves the API. Current features: first-run admin setup, login, workspace projects and media, workflow templates and database-backed settings. Video generation, rendering, publishing, paid billing and automated workflows are **not yet implemented**.

## Start locally with PostgreSQL

Requires Python 3.11+, Node.js 20.9+ and an existing PostgreSQL database. Create a dedicated database and user on your PostgreSQL server. Give the user permission to create tables in its own database/schema.

1. Copy `config.example.json` to `instance/bootstrap.json` and fill in the PostgreSQL connection URL. This is the **only backend bootstrap value outside PostgreSQL**: the app cannot discover a database connection by reading that database. Keep the file out of Git and restrict access to the service account. Do not create a `.env` file. The password in the connection URL should be URL-encoded if it contains reserved URL characters.
2. Start the API from the repository root:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
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

The database URL and Next.js-to-API address are deployment bootstrap details and cannot be stored exclusively in PostgreSQL without a separate service-discovery mechanism. Provider API keys are not accepted or stored yet. When those integrations are added, credentials must be encrypted before storage, with the encryption key kept outside the database.

For remote access, put HTTPS in front of the frontend, set `frontend_origin` and `secure_cookies` in System Settings, and keep the API and PostgreSQL private. Changing the frontend origin may require signing in again on the new address.

For an existing instance using `instance/config.json`, the backend reads it if `instance/bootstrap.json` is absent. Old `storage_dir`, `secure_cookies` and `frontend_origin` values are imported into the database once if settings rows do not exist. The old file can then be replaced with `instance/bootstrap.json` containing only `database_url`. Existing SQLite data must be migrated to PostgreSQL separately; changing the URL does not migrate data.

## Current architecture and next steps

- Projects, assets, workflows and workspace settings are scoped to the signed-in user's workspace. The first user is the system admin.
- Trial's project limit is enforced server-side from a database setting. There is no checkout, plan upgrade endpoint or open registration yet.
- Workflow records are templates only; the engine, FFmpeg workers, human review, scheduled publishing and usage/billing are planned next.
