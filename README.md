# ReelForge Studio

Self-hosted foundation for a short-video production platform. The architecture separates a Next.js/React/TypeScript dashboard from a FastAPI/Python API. Current features: first-run admin setup, session login, a workspace dashboard, projects, media uploads, and workflow templates. Video generation, rendering, publishing, paid billing and automated workflows are **not yet implemented**.

## Start locally

Requires Python 3.11+ and Node.js 20.9+.

**Backend** (terminal 1, from repository root):

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Frontend** (terminal 2):

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000 and create the first admin account. Do not use the backend port as the UI. Backend API documentation: http://127.0.0.1:8000/docs.

## Configuration without `.env`

Default: SQLite in `instance/reelforge.sqlite3`, uploads in `instance/media`, backend at `127.0.0.1:8000`, frontend at `localhost:3000`.

For PostgreSQL or custom storage, copy `config.example.json` to `instance/config.json` in the repository root. Set `database_url` to `postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME`. The file may contain database credentials: keep it out of Git and restrict permissions to the service account. Set `secure_cookies` to `true` under HTTPS and `frontend_origin` to the browser-visible frontend origin. The Next.js proxy reads `frontend/instance/config.json` if present; copy `frontend/config.example.json` there and set `api_base_url` to the backend address reachable by the Next.js server. Restart both services after changing these files.

The browser calls `/api/*` on the frontend origin, and Next.js forwards requests to FastAPI. Put HTTPS in front of both services for remote access, and expose the Next.js origin to users. Keep the database and media storage persistent and back them up together.

## Current architecture

- Projects, assets and workflows carry a workspace ID; endpoints scope reads and writes to the signed-in user's workspace.
- Trial has a provisional server-side limit of two projects. There is no checkout, plan upgrade endpoint, or user registration beyond initial setup.
- Media files are stored on disk, with metadata in SQLite or PostgreSQL. The storage directory and database should remain private.
- Workflow records are templates with steps, but no execution engine yet. The dashboard marks them as such.

## Next milestones

1. Database migrations, team roles, plan entitlements and usage records.
2. Script and scene editor; durable queue and idempotent workflow jobs.
3. FFmpeg rendering and human review.
4. Official YouTube/Facebook/TikTok integrations and scheduling, subject to each platform's API access.
5. Encrypted provider credentials and billing.
