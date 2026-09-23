# ReelForge Studio

An early self-hosted foundation for a short-video production studio. The current version provides first-run admin setup, session login, a workspace dashboard, projects, media uploads and workflow templates. Generation, rendering, publishing and payments are **not implemented yet**.

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and create the first admin account. The password must have at least 12 characters. With no configuration file, the app creates `instance/reelforge.sqlite3` and stores uploads in `instance/media`.

## PostgreSQL and configuration

There is no `.env` file. Create `instance/config.json` (excluded from Git) based on `config.example.json`. For PostgreSQL, set `database_url` to `postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME`. Restrict file permissions to the service account. Set `secure_cookies` to `true` when serving over HTTPS. Keep the database and media volume on persistent storage and back them up together. Never expose the app to the public internet over HTTP.

## Architecture

- Every project, media asset and workflow belongs to a workspace; API reads and writes enforce workspace scope.
- Trial currently limits projects to two. This is a provisional server-side entitlement; there is no paid checkout or plan activation endpoint.
- Media is stored on the filesystem and metadata is stored in SQLite or PostgreSQL.
- Workflow records are templates only. A durable queue, rendering worker, approval gate and official publisher integrations are planned next.

## Roadmap

1. Migrations, workspace roles, invitations and richer plan entitlements.
2. Script and scene editor; job runner with retries and idempotency.
3. FFmpeg rendering, review and approval.
4. OAuth and official YouTube/Facebook/TikTok publishing where account/API access permits.
5. Subscription billing, usage metering, provider credentials stored encrypted with an external master key.
