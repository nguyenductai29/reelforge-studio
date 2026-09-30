# ReelForge Studio — Home Server Deployment Guide

This guide describes how to deploy the `feat/studio-foundation` branch of ReelForge Studio to the IMO & KOME Ubuntu home server.

The target production layout is:

```text
Internet
  |
  v
Cloudflare Tunnel
  |
  +--> https://studio.imokome-cloud.com
          |
          v
      Next.js frontend
      127.0.0.1:3001
          |
          v
      FastAPI backend
      127.0.0.1:8000
          |
          v
      PostgreSQL
      127.0.0.1:5432
          |
          +--> reelforge_studio_db

Background services:
  - ReelForge video worker
  - ReelForge YouTube worker

Persistent media:
  /srv/data/reelforge/media
```

The frontend is the only service exposed through Cloudflare. FastAPI and PostgreSQL remain private to the home server.

---

## 1. Prerequisites

Expected home-server environment:

- Ubuntu Server
- Git
- Python 3.11 or newer
- Node.js 20.9 or newer
- npm
- Existing PostgreSQL server/container
- Cloudflare Tunnel already installed and running
- Domain managed by Cloudflare
- Browser SSH available through `https://ssh.imokome-cloud.com`

Check the main tools:

```bash
python3 --version
node --version
npm --version
git --version
docker --version
```

For the current home server, Python 3.14 is acceptable.

If Python virtual-environment support is missing:

```bash
sudo apt update
sudo apt install -y python3.14-venv
```

If the installed Python version is different, install the matching `pythonX.Y-venv` package.

---

## 2. Clone the deployment branch

Applications are stored under `~/apps`.

```bash
mkdir -p ~/apps
cd ~/apps
```

Clone the requested branch:

```bash
git clone \
  -b feat/studio-foundation \
  --single-branch \
  git@github.com:nguyenductai29/reelforge-studio.git
```

Enter the project:

```bash
cd ~/apps/reelforge-studio
```

Confirm the branch:

```bash
git branch --show-current
```

Expected:

```text
feat/studio-foundation
```

---

## 3. PostgreSQL database

ReelForge Studio uses its own PostgreSQL database and role.

Recommended values:

```text
Database: reelforge_studio_db
User:     studio_admin
```

Do not reuse `shop_kome_db`.

### 3.1 Create the PostgreSQL role and database

Open PostgreSQL using the existing administrator account:

```bash
docker exec -it postgres psql -U admin -d postgres
```

Inside `psql`:

```sql
CREATE ROLE studio_admin WITH LOGIN PASSWORD 'CHANGE_TO_A_STRONG_PASSWORD';
CREATE DATABASE reelforge_studio_db OWNER studio_admin;
```

Exit:

```sql
\q
```

Test the new database:

```bash
docker exec -it postgres psql \
  -U studio_admin \
  -d reelforge_studio_db
```

Then:

```sql
SELECT current_database(), current_user;
\q
```

Expected:

```text
reelforge_studio_db | studio_admin
```

### 3.2 Verify PostgreSQL is reachable locally

The ReelForge backend runs on the same home server, so use the local PostgreSQL endpoint:

```bash
nc -zv 127.0.0.1 5432
```

Expected:

```text
Connection to 127.0.0.1 5432 port [tcp/postgresql] succeeded!
```

Do not use `https://` with PostgreSQL or `nc`.

The public database hostname `db.imokome-cloud.com` is intended for external development clients. The SoftBank router may not support NAT loopback reliably, so testing that public hostname from the home LAN can time out even when external access works.

---

## 4. Backend bootstrap configuration

ReelForge intentionally does not keep the database connection in a normal `.env` file.

Create the private instance directory:

```bash
cd ~/apps/reelforge-studio
mkdir -p instance
```

Create:

```bash
nano instance/bootstrap.json
```

Example:

```json
{
  "database_url": "postgresql://studio_admin:URL_ENCODED_PASSWORD@127.0.0.1:5432/reelforge_studio_db?sslmode=require"
}
```

Important:

- Replace the password.
- URL-encode reserved characters in the password.
- Do not add `uselibpqcompat=true`; psycopg/libpq does not use that option.
- Keep `sslmode=require` because PostgreSQL TLS is enabled.

Restrict permissions:

```bash
chmod 600 instance/bootstrap.json
```

Verify:

```bash
ls -l instance/bootstrap.json
```

The repository already ignores `instance/`. Confirm before committing anything:

```bash
git status --short
cat .gitignore
```

Never commit `instance/bootstrap.json`.

---

## 5. Python virtual environment

From the repository root:

```bash
cd ~/apps/reelforge-studio
python3 -m venv .venv
```

Activate:

```bash
source .venv/bin/activate
```

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

Install backend dependencies:

```bash
pip install -r requirements.txt
```

Confirm Alembic:

```bash
python -m alembic --version
```

---

## 6. Database migrations

Always back up the PostgreSQL database before applying migrations to an existing installation.

Run migrations from the repository root:

```bash
cd ~/apps/reelforge-studio
source .venv/bin/activate
python -m alembic upgrade head
```

Check the active revision:

```bash
python -m alembic current
```

Verify tables directly:

```bash
docker exec -it postgres psql \
  -U studio_admin \
  -d reelforge_studio_db
```

Inside `psql`:

```sql
\dt
SELECT * FROM alembic_version;
\q
```

On a fresh database, use `upgrade head`. Do not use `stamp head` as a substitute for running migrations.

---

## 7. Test FastAPI manually once

Before installing the systemd service, verify the backend can start.

```bash
cd ~/apps/reelforge-studio
source .venv/bin/activate

python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000
```

From another terminal/browser-SSH session:

```bash
curl -I http://127.0.0.1:8000/docs
```

Or:

```bash
curl http://127.0.0.1:8000/openapi.json | head
```

Stop the manual server with `Ctrl+C` after the test.

---

## 8. FastAPI systemd service

Create:

```bash
sudo nano /etc/systemd/system/reelforge-api.service
```

Contents:

```ini
[Unit]
Description=ReelForge Studio FastAPI
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
User=tai
Group=tai
WorkingDirectory=/home/tai/apps/reelforge-studio

ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

Restart=always
RestartSec=5

Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Reload and enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-api
sudo systemctl start reelforge-api
```

Check:

```bash
systemctl is-enabled reelforge-api
systemctl is-active reelforge-api
sudo systemctl status reelforge-api
```

Expected:

```text
enabled
active
```

Logs:

```bash
journalctl -u reelforge-api -f
```

Recent logs:

```bash
journalctl -u reelforge-api -n 100 --no-pager
```

Test:

```bash
curl -I http://127.0.0.1:8000/docs
```

---

## 9. Frontend configuration

The frontend is Next.js.

Install dependencies:

```bash
cd ~/apps/reelforge-studio/frontend
npm ci
```

Create the ignored frontend instance directory:

```bash
mkdir -p instance
```

Create:

```bash
nano instance/config.json
```

For this single-server deployment:

```json
{
  "api_base_url": "http://127.0.0.1:8000"
}
```

This URL is used by the Next.js server to reach FastAPI. It is not a browser-public API URL.

Build the frontend:

```bash
npm run build
```

Test manually:

```bash
PORT=3001 npm start
```

In another terminal:

```bash
curl -I http://127.0.0.1:3001
```

Stop the manual process with `Ctrl+C` after the test.

---

## 10. Next.js systemd service

Create:

```bash
sudo nano /etc/systemd/system/reelforge-frontend.service
```

Contents:

```ini
[Unit]
Description=ReelForge Studio Next.js Frontend
After=network-online.target reelforge-api.service
Wants=network-online.target
Requires=reelforge-api.service

[Service]
Type=simple
User=tai
Group=tai
WorkingDirectory=/home/tai/apps/reelforge-studio/frontend

Environment=NODE_ENV=production
Environment=PORT=3001

ExecStart=/usr/bin/npm start

Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

If `npm` is not located at `/usr/bin/npm`, check:

```bash
which npm
```

and replace `ExecStart` with the returned absolute path.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-frontend
sudo systemctl start reelforge-frontend
```

Check:

```bash
systemctl is-enabled reelforge-frontend
systemctl is-active reelforge-frontend
sudo systemctl status reelforge-frontend
```

Test locally:

```bash
curl -I http://127.0.0.1:3001
```

Logs:

```bash
journalctl -u reelforge-frontend -f
```

### Create the first administrator

Do this on a fresh database before adding the public Cloudflare route in section 14. Until an account exists, whoever opens the sign-in page first can create the administrator.

```bash
cd ~/apps/reelforge-studio/frontend
npm run create-admin
```

The command needs the API running. It asks for the email and a password of at least 12 characters, hiding the password as you type, and creates the administrator with a Trial studio. It calls the same API as the sign-in page, at the address in `frontend/instance/config.json`. For a non-interactive release script, pass the credentials as environment variables instead:

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='a-long-password' npm run create-admin
```

Once an account exists, the command prints that there is nothing to do and exits successfully, so it is safe to keep in a deploy script.

---

## 11. Persistent media on the HDD

Video and uploaded media can consume substantial disk space. Use the HDD rather than the OS SSD.

Create:

```bash
sudo mkdir -p /srv/data/reelforge/media
sudo chown -R tai:tai /srv/data/reelforge
```

Verify:

```bash
ls -lah /srv/data/reelforge
```

After the application is running, configure the ReelForge media/storage path in System Settings to:

```text
/srv/data/reelforge/media
```

Back up the PostgreSQL database and this media directory together. Database rows contain asset metadata, while the binary media files live on disk.

---

## 12. Video worker systemd service

The video worker is required for queued AI video jobs.

Create:

```bash
sudo nano /etc/systemd/system/reelforge-video-worker.service
```

Contents:

```ini
[Unit]
Description=ReelForge Studio Video Worker
After=network-online.target reelforge-api.service
Wants=network-online.target
Requires=reelforge-api.service

[Service]
Type=simple
User=tai
Group=tai
WorkingDirectory=/home/tai/apps/reelforge-studio

ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.video_worker

Restart=always
RestartSec=5

Environment=PYTHONUNBUFFERED=1

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-video-worker
sudo systemctl start reelforge-video-worker
```

Check:

```bash
systemctl is-active reelforge-video-worker
journalctl -u reelforge-video-worker -f
```

Provider credentials such as `FAL_KEY`, `RUNWARE_API_KEY`, `REPLICATE_API_TOKEN`, or Runway credentials must be supplied to both the API and video worker when those providers are enabled.

Do not put private API keys into Git.

---

## 13. YouTube worker systemd service

The YouTube worker is needed only when YouTube OAuth and upload are configured.

Create:

```bash
sudo nano /etc/systemd/system/reelforge-youtube-worker.service
```

Contents:

```ini
[Unit]
Description=ReelForge Studio YouTube Worker
After=network-online.target reelforge-api.service
Wants=network-online.target
Requires=reelforge-api.service

[Service]
Type=simple
User=tai
Group=tai
WorkingDirectory=/home/tai/apps/reelforge-studio

ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.youtube_worker

Restart=always
RestartSec=5

Environment=PYTHONUNBUFFERED=1

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable when YouTube integration is ready:

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-youtube-worker
sudo systemctl start reelforge-youtube-worker
```

The API and YouTube worker need the same Google OAuth configuration and persistent `REELFORGE_TOKEN_ENCRYPTION_KEY`.

Back up that encryption key securely. Losing it makes stored encrypted OAuth tokens and resumable upload sessions unreadable.

---

## 14. Cloudflare Tunnel

Only the frontend needs a public route.

In Cloudflare Zero Trust:

```text
Networks
  -> Tunnels & Mesh
  -> tai-home-server
  -> Published application routes
  -> Add a published application route
```

Use:

```text
Subdomain: studio
Domain:    imokome-cloud.com
Service:   http://localhost:3001
```

Result:

```text
https://studio.imokome-cloud.com
```

Do not create public routes for:

```text
127.0.0.1:8000   FastAPI
127.0.0.1:5432   PostgreSQL
workers
```

Cloudflare terminates public HTTPS. Next.js communicates privately with FastAPI on localhost.

After the public hostname is working, configure ReelForge System Settings:

```text
frontend_origin = https://studio.imokome-cloud.com
secure_cookies  = true
```

Changing the frontend origin may require signing in again.

Test:

```bash
curl -I https://studio.imokome-cloud.com
```

---

## 15. Optional payOS configuration

Keep payOS credentials only in the private backend bootstrap file.

Example structure:

```json
{
  "database_url": "postgresql://studio_admin:URL_ENCODED_PASSWORD@127.0.0.1:5432/reelforge_studio_db?sslmode=require",
  "payos": {
    "client_id": "YOUR_CLIENT_ID",
    "api_key": "YOUR_API_KEY",
    "checksum_key": "YOUR_CHECKSUM_KEY"
  }
}
```

The production webhook endpoint must be public HTTPS:

```text
https://studio.imokome-cloud.com/api/webhooks/payos
```

Verify the actual proxy path and callback configuration before accepting payments.

Never commit real payOS credentials.

---

## 16. Updating the deployment

Recommended update procedure:

```bash
cd ~/apps/reelforge-studio

git status
git pull

source .venv/bin/activate
pip install -r requirements.txt
python -m alembic upgrade head

cd frontend
npm ci
npm run build
cd ..

sudo systemctl restart reelforge-api
sudo systemctl restart reelforge-frontend
```

If video-worker code or Python dependencies changed:

```bash
sudo systemctl restart reelforge-video-worker
```

If YouTube-worker code or dependencies changed:

```bash
sudo systemctl restart reelforge-youtube-worker
```

Verify:

```bash
systemctl is-active reelforge-api
systemctl is-active reelforge-frontend

curl -I http://127.0.0.1:8000/docs
curl -I http://127.0.0.1:3001
curl -I https://studio.imokome-cloud.com
```

---

## 17. Recommended deploy script

A simple deploy helper can live at:

```text
~/apps/reelforge-studio/deploy.sh
```

Example:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "== Pull source =="
git pull

echo "== Backend dependencies =="
source .venv/bin/activate
python -m pip install -r requirements.txt

echo "== Database migrations =="
python -m alembic upgrade head

echo "== Frontend dependencies/build =="
cd frontend
npm ci
npm run build
cd ..

echo "== Restart services =="
sudo systemctl restart reelforge-api
sudo systemctl restart reelforge-frontend

if systemctl is-enabled --quiet reelforge-video-worker 2>/dev/null; then
  sudo systemctl restart reelforge-video-worker
fi

if systemctl is-enabled --quiet reelforge-youtube-worker 2>/dev/null; then
  sudo systemctl restart reelforge-youtube-worker
fi

echo "== Health checks =="
curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null
curl -fsSI http://127.0.0.1:3001 >/dev/null

echo "Deploy completed."
```

Make executable:

```bash
chmod +x deploy.sh
```

Note: restarting services through this script requires the current user to have permission to run the corresponding `sudo systemctl` commands.

---

## 18. Service management cheat sheet

Backend:

```bash
sudo systemctl start reelforge-api
sudo systemctl stop reelforge-api
sudo systemctl restart reelforge-api
sudo systemctl status reelforge-api
journalctl -u reelforge-api -f
```

Frontend:

```bash
sudo systemctl start reelforge-frontend
sudo systemctl stop reelforge-frontend
sudo systemctl restart reelforge-frontend
sudo systemctl status reelforge-frontend
journalctl -u reelforge-frontend -f
```

Video worker:

```bash
sudo systemctl restart reelforge-video-worker
journalctl -u reelforge-video-worker -f
```

YouTube worker:

```bash
sudo systemctl restart reelforge-youtube-worker
journalctl -u reelforge-youtube-worker -f
```

All ReelForge services:

```bash
systemctl --type=service --all | grep reelforge
```

---

## 19. Reboot verification

After the deployment is stable:

```bash
sudo reboot
```

Reconnect through:

```text
https://ssh.imokome-cloud.com
```

Then verify:

```bash
systemctl is-active reelforge-api
systemctl is-active reelforge-frontend
systemctl is-active reelforge-video-worker

curl -I http://127.0.0.1:8000/docs
curl -I http://127.0.0.1:3001
```

If YouTube worker is enabled:

```bash
systemctl is-active reelforge-youtube-worker
```

---

## 20. Security checklist

Before treating the service as production-ready:

- Keep `instance/bootstrap.json` outside Git.
- Create the administrator with `npm run create-admin` before the public route exists.
- Use a strong unique PostgreSQL password for `studio_admin`.
- Keep FastAPI bound to `127.0.0.1:8000`.
- Keep Next.js bound to localhost; expose only through Cloudflare Tunnel.
- Do not expose the worker processes.
- Set `frontend_origin` to the HTTPS production hostname.
- Enable secure cookies after HTTPS is active.
- Keep provider, payOS, Google OAuth and Fernet secrets out of the repository.
- Back up PostgreSQL and `/srv/data/reelforge/media` together.
- Back up `REELFORGE_TOKEN_ENCRYPTION_KEY` separately and securely.
- Apply Alembic migrations before restarting application services after an update.
- Review application and Cloudflare request-body/rate limits before public use.

---

## 21. Production endpoints

Expected production endpoints:

```text
Frontend:
https://studio.imokome-cloud.com

Browser SSH:
https://ssh.imokome-cloud.com

Backend, private:
http://127.0.0.1:8000

Frontend service, private:
http://127.0.0.1:3001

PostgreSQL, local backend connection:
127.0.0.1:5432

PostgreSQL database:
reelforge_studio_db

PostgreSQL user:
studio_admin

Media:
 /srv/data/reelforge/media
```

For external developer database access, use the separately configured PostgreSQL public endpoint only when required. Production ReelForge services on the home server should use the local PostgreSQL connection.
