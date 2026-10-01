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

The Render step (Phase 8) needs FFmpeg and, for burned-in Vietnamese or Japanese subtitles, fonts that cover them:

```bash
sudo apt install -y ffmpeg fonts-noto-core fonts-noto-cjk
ffmpeg -version | head -1
ffprobe -version | head -1
fc-list : family | grep -i "noto sans" | head -3
```

The API and every worker check that `ffmpeg` and `ffprobe` exist before they queue a render, so install FFmpeg on the machine that runs them (or set `RENDER_FFMPEG_PATH` / `RENDER_FFPROBE_PATH` in the shared runtime file). ReelForge never bundles FFmpeg.

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

### 4.1 Runtime environment file (provider keys)

Provider keys, prices and the YouTube OAuth settings are environment variables. The API and every worker need the same values, because a worker that finishes one step also starts the next one. Keep them in one root-owned file that every unit loads:

```bash
sudo mkdir -p /etc/reelforge
sudo cp ~/apps/reelforge-studio/.env.runtime.example /etc/reelforge/runtime.env
sudo chown root:tai /etc/reelforge/runtime.env
sudo chmod 640 /etc/reelforge/runtime.env
sudo nano /etc/reelforge/runtime.env
```

Fill in only the providers you use. The format is `KEY=value` per line, without `export`. Every unit below has `EnvironmentFile=/etc/reelforge/runtime.env`. Check the file without calling any provider:

```bash
cd ~/apps/reelforge-studio
REELFORGE_ENV_FILE=/etc/reelforge/runtime.env .venv/bin/python -m app.provider_check
```

After editing the file, restart the API and all workers. Each logs a `process_started` line with key fingerprints (never the keys). `journalctl -u 'reelforge-*' | grep process_started` shows whether every process loaded the same keys. See `docs/LIVE_PROVIDER_SMOKE_TEST.md` for the live smoke tests.

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
EnvironmentFile=/etc/reelforge/runtime.env

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
EnvironmentFile=/etc/reelforge/runtime.env

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

Provider credentials such as `FAL_KEY`, `RUNWARE_API_KEY`, `REPLICATE_API_TOKEN`, Runway credentials, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` must be supplied to the API, the video worker and the text worker alike, through the shared `/etc/reelforge/runtime.env` (section 4.1). A worker that finishes one step also starts the next one: for example, the text worker queues the video step that follows an AI Writer, and blocks it if it cannot see that video provider's key.

Do not put private API keys into Git.

### Text worker

The text worker runs the AI Writer, Summarize, Rewrite, Translate, Hook, Title and CTA steps. Create `/etc/systemd/system/reelforge-text-worker.service` with the same contents as the video worker, changing only these two lines:

```ini
Description=ReelForge Studio Text Worker
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.text_worker
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-text-worker
sudo systemctl start reelforge-text-worker
journalctl -u reelforge-text-worker -f
```

The text worker gets `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` and `TEXT_CREDITS_PER_GENERATION` (default 1, credits per text step) from the same `EnvironmentFile` as the API, because it copies the video worker unit.

### Image worker

The image worker runs Image steps (one image per scene, or the chosen number of images from one prompt; see `docs/IMAGE_GENERATION.md`). Create `/etc/systemd/system/reelforge-image-worker.service` with the same contents as the video worker, changing only these two lines:

```ini
Description=ReelForge Studio Image Worker
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.image_worker
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-image-worker
sudo systemctl start reelforge-image-worker
journalctl -u reelforge-image-worker -f
```

It needs `RUNWAYML_API_SECRET` and `RUNWAY_OUTPUT_HOSTS` (the same Runway credentials video uses), and optionally `IMAGE_CREDITS_PER_GENERATION` (default 2 credits per image) and `IMAGE_JOB_MAX_AGE_SECONDS` (default 3600), from the same `EnvironmentFile`. `deploy.sh` restarts it when the unit is enabled. It writes to the same media directory as the video worker.

Multi-scene video needs no new process: the video worker also runs the one-clip-per-scene jobs (see `docs/MULTI_SCENE_VIDEO.md`).

### Voice worker

The voice worker runs Voice steps: one narration for a script, or one per scene (see `docs/VOICE_GENERATION.md`). Create `/etc/systemd/system/reelforge-voice-worker.service` with the same contents as the video worker, changing only these two lines:

```ini
Description=ReelForge Studio Voice Worker
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.voice_worker
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-voice-worker
sudo systemctl start reelforge-voice-worker
journalctl -u reelforge-voice-worker -f
```

It needs `GEMINI_API_KEY` (the same key Gemini text uses), and optionally `VOICE_CREDITS_PER_GENERATION` (default 1 credit per narration) and `VOICE_JOB_MAX_AGE_SECONDS` (default 1800), from the same `EnvironmentFile`.

### Render worker

The render worker joins clips, narration and subtitles into the final MP4 with FFmpeg (see `docs/RENDERING.md`). Install FFmpeg and the fonts first (section 1), then create `/etc/systemd/system/reelforge-render-worker.service` with the same contents as the video worker, changing only these two lines:

```ini
Description=ReelForge Studio Render Worker
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.render_worker
```

Check the tools and font, then enable it:

```bash
cd /home/tai/apps/reelforge-studio && .venv/bin/python -m app.render_worker --check
sudo systemctl daemon-reload
sudo systemctl enable reelforge-render-worker
sudo systemctl start reelforge-render-worker
journalctl -u reelforge-render-worker -f
```

Rendering is local and free by default (`RENDER_CREDITS_PER_JOB=0`). `RENDER_TIMEOUT_SECONDS` (default 1800) stops a stuck FFmpeg. Temporary files live in `<media>/.render-tmp/<job_id>` and are removed after each render; a folder left by a killed worker can be deleted when no render is running. Render uses CPU heavily: on a small server, keep one render worker.

Subtitles need no worker: the Subtitle step writes its SRT/VTT file while the run advances.

The render worker also cuts Movie Recap source clips (`clips.extract` jobs, `docs/MOVIE_RECAP.md`), with the same FFmpeg and temporary folder.

### Source worker

The source worker fetches web pages for URL Source steps and transcribes audio and video for Transcript steps (`docs/CONTENT_SOURCES.md`). Transcription needs FFmpeg (section 1) and `OPENAI_API_KEY`. Create `/etc/systemd/system/reelforge-source-worker.service` with the same contents as the video worker, changing only:

```ini
Description=ReelForge Studio Source Worker
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.source_worker
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-source-worker
sudo systemctl start reelforge-source-worker
journalctl -u reelforge-source-worker -f
```

Optional variables: `TRANSCRIPTION_CREDITS_PER_JOB` (default 2) and `TRANSCRIPTION_MAX_SECONDS` (default 10800). Transcription audio is extracted to `<media>/.source-tmp/<job_id>` and removed after each job. The worker reaches only public `https://` pages; it refuses private, local and metadata addresses (see `docs/CONTENT_SOURCES.md`), so it needs no access to the local network.

---

## 13. YouTube worker systemd service

The YouTube worker is needed only when YouTube OAuth and upload are configured.

The full social-video workflow (`docs/SOCIAL_VIDEO_WORKFLOW.md`) runs on these processes:

- the API and frontend;
- the text worker (AI Writer and Metadata), the video worker, the voice worker and the render worker (FFmpeg and fonts from section 1);
- this YouTube worker, for publishing.

Subtitles need no worker. Publishing needs no new process or variable: visibility (private, unlisted or public) and tags use the same `youtube.upload` scope. Google keeps uploads from unverified API projects private, and the Publishing page shows the visibility YouTube applied. Apply migration `0013_publication_metadata` with `python -m alembic upgrade head`; `deploy.sh` already does this.

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
EnvironmentFile=/etc/reelforge/runtime.env

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

### Social worker (TikTok and Facebook)

The social worker uploads approved videos to TikTok (as inbox drafts) and Facebook Page Reels through their official APIs (`docs/MULTI_PLATFORM_PUBLISHING.md`). It needs, in the same runtime file as the API:

- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` (`https://<your frontend>/channels/callback/tiktok`);
- `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`, `FACEBOOK_REDIRECT_URI` (`https://<your frontend>/channels/callback/facebook`);
- the same `REELFORGE_TOKEN_ENCRYPTION_KEY`.

Configure only the platforms you use; the Channels page shows the others as needing server configuration. Create `/etc/systemd/system/reelforge-social-worker.service` with the same contents as the YouTube worker, changing only:

```ini
Description=ReelForge Studio Social Worker
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.social_worker
```

### Scheduler worker

The scheduler queues scheduled publications when their time comes (`docs/SCHEDULING.md`). It is small and safe to restart. Create `/etc/systemd/system/reelforge-scheduler-worker.service` with the same contents as the YouTube worker, changing only:

```ini
Description=ReelForge Studio Scheduler
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.scheduler_worker
```

Enable both:

```bash
sudo systemctl daemon-reload
sudo systemctl enable reelforge-social-worker reelforge-scheduler-worker
sudo systemctl start reelforge-social-worker reelforge-scheduler-worker
```

Apply migration `0014_channels_scheduling_ops` with `python -m alembic upgrade head` (`deploy.sh` does this) before starting them. **Admin → Operations** shows every worker's heartbeat; a worker that has never started shows as missing.

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

for worker in text image video voice render source youtube social scheduler; do
  if systemctl is-enabled --quiet "reelforge-$worker-worker" 2>/dev/null; then
    sudo systemctl restart "reelforge-$worker-worker"
  fi
done

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

Text worker:

```bash
sudo systemctl restart reelforge-text-worker
journalctl -u reelforge-text-worker -f
```

Image, voice and render workers:

```bash
sudo systemctl restart reelforge-image-worker reelforge-voice-worker reelforge-render-worker
journalctl -u reelforge-render-worker -f
```

YouTube worker:

```bash
sudo systemctl restart reelforge-youtube-worker
journalctl -u reelforge-youtube-worker -f
```

Source, social and scheduler workers:

```bash
sudo systemctl restart reelforge-source-worker reelforge-social-worker reelforge-scheduler-worker
journalctl -u reelforge-social-worker -f
```

Media cleanup (dry run first; see `docs/OPERATIONS.md`):

```bash
cd /home/tai/apps/reelforge-studio && .venv/bin/python -m app.media_maintenance
.venv/bin/python -m app.media_maintenance --apply
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

If the source, social or scheduler workers are enabled:

```bash
systemctl is-active reelforge-source-worker reelforge-social-worker reelforge-scheduler-worker
```

**Admin → Operations** should then show every enabled worker as running within a minute.

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
