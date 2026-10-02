# ReelForge Studio — Home Server Deployment Guide

This guide describes how to deploy the `feat/studio-foundation` branch of ReelForge Studio to the IMO & KOME Ubuntu home server.

> **Phase 21: a new server needs no `.env.runtime`.** Normal operation needs only:
>
> - the database URL (`instance/bootstrap.json`);
> - the master key file (`/etc/reelforge/master.key`).
>
> Everything else is configured in the admin UI and read from PostgreSQL without restarts. The concise from-zero sequence is [PRODUCTION_BOOTSTRAP.md](PRODUCTION_BOOTSTRAP.md), and the systemd units are in `deploy/systemd/`. This guide gives the full detail. Where it still mentions `/etc/reelforge/runtime.env`, that is an optional legacy fallback for installations configured before Phase 20.

The target production layout is:

```text
Internet
  |
  v
Cloudflare Tunnel
  |
  +--> https://reelforge.mul-service.com
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

Background services (deploy/systemd/reelforge-worker@.service, one instance each):
  - text, image, video, voice, render, source, youtube, social, scheduler workers
  - daily media maintenance (timer, 03:00)

SSD: OS, application + virtualenv, PostgreSQL, /etc/reelforge (master.key)

Only https://reelforge.mul-service.com is public (and HTTPS). The frontend service
(http://127.0.0.1:3001) and the API (http://127.0.0.1:8000) listen on loopback only.
Persistent media (HDD at /srv/data):
  /srv/data/videos/reelforge   (media root: Admin → Cài đặt hệ thống → Lưu trữ)
  /srv/data/backups/reelforge  (database dumps; copy them off this disk too)
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

Do not add `frontend_origin` or `secure_cookies` to this file on the server.

- Those keys are a development machine's override (for `http://localhost:3000`).
- Production uses System Settings, which default to `https://reelforge.mul-service.com` with Secure cookies.
- `deploy.sh` stops if it finds them.

### 4.1 Master key file (the only other bootstrap)

Since Phase 20, ReelForge needs exactly two things outside PostgreSQL: the database URL above, and one **master encryption key file**. Every secret stored in the database is encrypted with that key: OAuth tokens, payment credentials, AI provider keys and OAuth app secrets. Everything else is configured in **Quản trị → Cài đặt hệ thống** and **Quản trị → Thanh toán → Cổng thanh toán**, without SSH or restarts. See [SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md).

Create it once, as the account the services run as (`tai` here):

```bash
sudo mkdir -p /etc/reelforge
sudo chown root:tai /etc/reelforge
sudo chmod 750 /etc/reelforge
cd ~/apps/reelforge-studio
sudo -u tai env REELFORGE_MASTER_KEY_FILE=/etc/reelforge/master.key .venv/bin/python -m app.master_key init
ls -l /etc/reelforge/master.key   # -rw------- tai
.venv/bin/python -m app.master_key status
```

**What `init` does:**

- **On a fresh installation**, it generates a key.
- **On an installation that already has `REELFORGE_TOKEN_ENCRYPTION_KEY`** in `/etc/reelforge/runtime.env`, it copies that key into the file, so everything stays readable.
- **If the database already holds encrypted data and no key is found**, it refuses rather than make that data unreadable.

`/etc/reelforge/master.key` is the default path, so the units need no variable for it. Set `REELFORGE_MASTER_KEY_FILE` only for another path.

**Back the key file up off the server**, separately from the database dumps. Never change it on a running installation.

- With the database but without the key, every secret must be re-entered and every channel reconnected.
- Without the file, the API still starts, and Admin → Kiểm định and Cài đặt hệ thống → Bảo mật say what is wrong.

### 4.2 Legacy runtime environment file (optional)

Installations configured before Phase 20 keep `/etc/reelforge/runtime.env`. Every setting nobody saved in the admin UI still reads its variable from it, and a saved value always wins.

- **New installations do not need it.** Leave it out, or keep only the optional `REELFORGE_LOG_FORMAT` and `REELFORGE_LOG_LEVEL`.
- **Make it optional in the units.** Every unit below uses `EnvironmentFile=-/etc/reelforge/runtime.env`; the leading `-` lets a unit start when the file does not exist.

To check what a legacy file still provides without calling any provider:

```bash
cd ~/apps/reelforge-studio
REELFORGE_ENV_FILE=/etc/reelforge/runtime.env .venv/bin/python -m app.provider_check
```

Each process logs a `process_started` line with the key fingerprints the environment provides, never the keys. Admin → Cài đặt hệ thống shows each setting's source (Admin, Môi trường or Mặc định).

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
EnvironmentFile=-/etc/reelforge/runtime.env

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

ExecStart=/usr/bin/npm start -- --hostname 127.0.0.1

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

`--hostname 127.0.0.1` keeps the frontend private: `next start` listens on every interface otherwise. Only the Cloudflare Tunnel reaches it.

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
ss -ltn | grep 3001     # 127.0.0.1:3001, not 0.0.0.0 or *
```

Logs:

```bash
journalctl -u reelforge-frontend -f
```

### Create the first administrator

Do this on a fresh database before adding the public Cloudflare route in section 14. The API accepts first-run setup only from the server itself (loopback or a private address): through Cloudflare the sign-in page shows how to create the administrator instead of a form, and the API answers 403. `deploy.sh` and `/health/ready` remind you while no account exists.

```bash
cd ~/apps/reelforge-studio/frontend
npm run create-admin
```

The command needs the API running. It asks for the email and a password of at least 12 characters, hiding the password as you type, and creates the administrator with a Trial studio. It calls the same API as the sign-in page, at the address in `frontend/instance/config.json`. For a non-interactive release script, pass the credentials as environment variables instead:

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='a-long-password' npm run create-admin
```

Once an account exists, the command prints that there is nothing to do and exits successfully, so it is safe to keep in a deploy script.

At the first sign-in the administrator is asked to accept the Terms of Service and the Privacy Policy (a banner), and to turn on two-factor authentication: **Cài đặt → Bảo mật → Bật 2FA**. Store the ten recovery codes off the server (see section 22).

---

## 11. Persistent media on the HDD

Keep the OS, the application, PostgreSQL and Docker/system files on the SSD. Put media and backups on the HDD, mounted at `/srv/data`; PostgreSQL metadata is small next to the video files.

```text
/srv/data/
├── backups/reelforge/   PostgreSQL dumps
├── images/              other projects
├── uploads/             other projects
└── videos/reelforge/    every ReelForge media file (one storage root)
```

ReelForge keeps all of its media (uploads, generated images, voice, clips and final videos) under one root, named by workspace and asset IDs only: `<root>/<workspace_id>/<asset_id>`. One root keeps quotas, lineage and cleanup in one place. See [STORAGE.md](STORAGE.md).

Create the folders and point every process at the root:

```bash
sudo mkdir -p /srv/data/videos/reelforge /srv/data/backups/reelforge
sudo chown -R tai:tai /srv/data/videos/reelforge /srv/data/backups/reelforge
# Then set the media root in Quản trị → Cài đặt hệ thống → Lưu trữ: /srv/data/videos/reelforge
# (legacy installations: REELFORGE_STORAGE_ROOT in /etc/reelforge/runtime.env still works until a value is saved)
```

`REELFORGE_STORAGE_ROOT` takes precedence over the stored `storage_dir` setting. Settings → Storage shows the folder in use to the system admin. Mount the HDD itself at `/srv/data` rather than linking the root: cleanup refuses to delete through symbolic links.

**Upgrading an installation that used `/srv/data/reelforge/media`:** either keep it by setting `REELFORGE_STORAGE_ROOT=/srv/data/reelforge/media`, or move it:

1. Stop the API and every worker.
2. Run `rsync -a /srv/data/reelforge/media/ /srv/data/videos/reelforge/`.
3. Set the variable and start the services.
4. Check that media opens, then remove the old folder.

**Quotas.** Each plan has a storage limit (Trial 1 GB, Standard 10 GB, Pro 30 GB after migration 0016; edit them in Admin → Plans). Keep the sum you expect to sell within the HDD's capacity. `WORKSPACE_MEDIA_QUOTA_BYTES`, when set, caps every studio.

**Backups.**

- PostgreSQL is dumped daily to `/srv/data/backups/reelforge` by `reelforge-backup.timer` (section 22, [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md)).
- A backup on the same HDD does **not** protect against that disk failing. Copy the dumps to another machine or disk too, and keep the master key (`/etc/reelforge/master.key`) off the server **separately** from them.
- Do not duplicate the full video tree on the same HDD by default. If the videos matter, `rsync` them to a second disk or host.

### Daily media cleanup (03:00)

Intermediate media (scene clips, narration, generated images, extracted clips) of runs that have their final video expires after 30 days. Worker leftovers expire after 1–3 days. Final videos and uploads are never removed automatically.

Create `/etc/systemd/system/reelforge-media-maintenance.service`:

```ini
[Unit]
Description=ReelForge Studio daily media maintenance
After=network-online.target

[Service]
Type=oneshot
User=tai
Group=tai
WorkingDirectory=/home/tai/apps/reelforge-studio
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.media_maintenance --apply --intermediates
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/reelforge/runtime.env
Nice=10
IOSchedulingClass=idle
```

And `/etc/systemd/system/reelforge-media-maintenance.timer`:

```ini
[Unit]
Description=Run ReelForge media maintenance daily

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Preview once, then enable:

```bash
cd ~/apps/reelforge-studio
REELFORGE_ENV_FILE=/etc/reelforge/runtime.env .venv/bin/python -m app.media_maintenance --intermediates
sudo systemctl daemon-reload
sudo systemctl enable --now reelforge-media-maintenance.timer
systemctl list-timers reelforge-media-maintenance.timer
journalctl -u reelforge-media-maintenance
```

The retention ages are `REELFORGE_RETENTION_*` in the runtime file. Set `REELFORGE_RETENTION_INTERMEDIATE_DAYS=0` to keep intermediates.

---

## 12. Worker services

**Recommended since Phase 21: one template unit for every worker.** `deploy/systemd/reelforge-worker@.service` runs `python -m app.<name>_worker` as `tai`, with no provider variable. Workers read their keys and settings from PostgreSQL and pick up changes within 15 seconds.

```bash
sudo cp deploy/systemd/reelforge-worker@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now reelforge-worker@{text,image,video,voice,render,source,youtube,social,scheduler}
journalctl -u 'reelforge-worker@*' -f
```

Enable only the workers this server needs.

**Switching from the per-worker units below:**

1. `sudo systemctl disable --now reelforge-<name>-worker`;
2. enable `reelforge-worker@<name>`.

`deploy.sh` restarts either form. The per-worker units below remain valid.

### Video worker (per-worker unit)

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
EnvironmentFile=-/etc/reelforge/runtime.env

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

Provider credentials (OpenAI, Anthropic, Gemini, Runway, fal, Runware, Replicate) are entered once in **Quản trị → Cài đặt hệ thống → Nhà cung cấp AI**. The API and every worker read them from the database, so they always agree. A changed key reaches workers within 15 seconds, with no restart. A worker that finishes one step also starts the next one: for example, the text worker queues the video step that follows an AI Writer, and blocks it if that video provider has no key. Legacy installations may still provide them through `/etc/reelforge/runtime.env` (section 4.2).

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

The text worker reads the text provider keys and the credit price per text step (Cài đặt hệ thống → Giá credits, default 1) from the database, like the API.

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

It needs the Runway secret and output hosts (Cài đặt hệ thống → Nhà cung cấp AI → Runway, the same credentials video uses). The credits per image (default 2) and the image job wait (default 3600 s) are in Giá credits and Runtime. `deploy.sh` restarts it when the unit is enabled. It writes to the same media directory as the video worker.

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

It needs the Gemini key (the same key Gemini text uses). The credits per narration (default 1) and the voice job wait (default 1800 s) are in Giá credits and Runtime.

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
EnvironmentFile=-/etc/reelforge/runtime.env

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

The API and the YouTube worker read the Google OAuth app from Admin → Cài đặt hệ thống → OAuth mạng xã hội (or the legacy variables) and decrypt the stored tokens with the same master key (`/etc/reelforge/master.key`).

Back that key up securely, off the server. Losing it makes stored OAuth tokens and resumable upload sessions unreadable.

### Social worker (TikTok and Facebook)

The social worker uploads approved videos to TikTok (as inbox drafts) and Facebook Page Reels through their official APIs (`docs/MULTI_PLATFORM_PUBLISHING.md`). It needs, in the same runtime file as the API:

- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` (`https://<your frontend>/channels/callback/tiktok`);
- `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`, `FACEBOOK_REDIRECT_URI` (`https://<your frontend>/channels/callback/facebook`);
- the same master key as the API (`/etc/reelforge/master.key`).

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
Service:   http://127.0.0.1:3001
```

Result:

```text
https://reelforge.mul-service.com
```

Do not create public routes for:

```text
127.0.0.1:8000   FastAPI
127.0.0.1:5432   PostgreSQL
workers
```

Cloudflare terminates public HTTPS. Next.js communicates privately with FastAPI on localhost.

ReelForge System Settings already hold these values. They are the defaults of a new installation, and migration `0021_default_production_origin` sets them on an installation still on the old localhost defaults:

```text
frontend_origin = https://reelforge.mul-service.com
secure_cookies  = true
```

Check them in Quản trị → Cài đặt hệ thống → Chung, and change them there only for another hostname. Changing the frontend origin may require signing in again.

Test:

```bash
curl -I https://reelforge.mul-service.com
```

### Realtime notifications (Server-Sent Events)

The notification bell keeps one long-lived request open per browser tab: `GET /api/notifications/stream`. It needs no new route, port or process. It travels Cloudflare → `127.0.0.1:3001` (Next.js) → `127.0.0.1:8000` (FastAPI), like every other `/api/*` request, and authenticates with the session cookie.

**What keeps it working through each hop:**

- **The FastAPI response**
  - It sends `Cache-Control: no-cache, no-transform`, so neither Next.js nor Cloudflare compresses it. Compression would hold events back.
  - It sends `X-Accel-Buffering: no` for nginx.
- **Next.js**
  - The rewrite proxy drops a connection that stays silent for 30 s.
  - The stream writes a `: ping` after 15 s of silence, which also keeps Cloudflare's idle limit of about 100 s away.
  - Tested: a stream through `next start` stayed open past 30 s, with pings every 15 s.
- **Reconnecting**
  - The API ends each stream after 5 minutes (`REELFORGE_SSE_MAX_SECONDS`).
  - The browser reconnects after 5 s and resumes from the last event (`Last-Event-ID`), so nothing is missed across a reconnect, a tunnel restart or an API restart.
  - While the stream is down, the bell polls every 30 s.

Cloudflare Tunnel needs no setting for this. If you put **nginx** in front of Next.js or the API, give the stream its own unbuffered location:

```nginx
location /api/notifications/stream {
    proxy_pass http://127.0.0.1:3001;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_buffering off;
    proxy_cache off;
    gzip off;
    proxy_read_timeout 1h;
}
```

**Check it:**

1. Open **Admin → Kiểm định → Kiểm tra luồng thông báo** from a browser on the public domain. It passes when the first event arrives.
2. From the server:

   ```bash
   # Sign in with a real account first, then stream for 40 s; expect "event: unread" at once and ": ping" every 15 s.
   curl -sN -b cookies.txt --max-time 40 https://reelforge.mul-service.com/api/notifications/stream
   ```

Each open stream checks the database every `REELFORGE_SSE_POLL_SECONDS` (default 3) with small indexed queries. Readiness in Admin → Kiểm định shows how many are open. See [NOTIFICATIONS.md](NOTIFICATIONS.md).

---

## 15. Payment gateways (payOS VietQR and OnePAY cards)

Since Phase 19, a system admin configures both gateways in the web UI: **Quản trị → Thanh toán → Cổng thanh toán**.

- **No SSH, no restart.** A saved change applies to the next checkout and callback.
- **Secrets are write-only.** They are encrypted at rest and never shown again.

See [PAYMENTS.md](PAYMENTS.md#configuration-phase-19-admin-managed).

### One-time server prerequisite: the master key

Admin-managed credentials are encrypted with the master key file of section 4.1 (`/etc/reelforge/master.key`), the same key that protects OAuth tokens. Create it before configuring payments. An installation that still has only `REELFORGE_TOKEN_ENCRYPTION_KEY` keeps working; move it into the file with `python -m app.master_key init`.

### Register the callback URLs

The admin dialog shows these with copy buttons. They come from System Settings → `frontend_origin`.

```text
payOS webhook:  https://reelforge.mul-service.com/api/webhooks/payos
OnePAY IPN:     https://reelforge.mul-service.com/api/webhooks/onepay
OnePAY return:  https://reelforge.mul-service.com/api/billing/onepay/return
```

They pass through Cloudflare Tunnel and Next.js like every other `/api/*` request; no extra route is needed.

### Configure

1. **Prices.** In **Quản trị → Cấu hình gói**, set the Standard and Pro prices. A plan without a price shows "Not purchasable: no price".
2. **VietQR.** Open **Cổng thanh toán → VietQR**. Enter the Client ID, API Key and Checksum Key, then press **Lưu** and **Kiểm tra cấu hình**.
3. **Card, sandbox first.** Open **Cổng thanh toán → Thẻ**, choose **Sandbox** and enter OnePAY's test merchant values. Press **Lưu**, **Kiểm tra cấu hình** and **Kiểm tra với OnePAY (QueryDR)**. Pay with the test card. Follow [Sandbox → production](PAYMENTS.md#sandbox--production-onepay).
4. **Card, production.** Enter the production values, choose **Production** and confirm.
5. **Record it.** Set each step's status in **Kiểm định** ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)).

**Disabling.** **Tắt** stops new checkouts for a gateway immediately. Pending orders still settle by webhook, IPN or **Check**.

### Legacy configuration (still supported)

Deployments configured before Phase 19 keep working without any change. The admin dialog shows them as *Configured from: Bootstrap* or *Environment*, and they can be disabled there.

As soon as an admin saves credentials for a gateway, those take precedence and the legacy values are ignored for it; they are not imported. Changing legacy values still needs the file edit and `sudo systemctl restart reelforge-api`.

**payOS** in `instance/bootstrap.json`:

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

**OnePAY** in `/etc/reelforge/runtime.env` (section 4.2):

```text
ONEPAY_MERCHANT_ID=...
ONEPAY_ACCESS_CODE=...
ONEPAY_HASH_KEY=...            # hex
ONEPAY_QUERY_USER=...          # QueryDR, recommended
ONEPAY_QUERY_PASSWORD=...
# ONEPAY_PAYMENT_URL / ONEPAY_QUERY_URL default to OnePAY production; set the sandbox URLs while testing.
```

Never commit real credentials.

### Safety

- **Payment proof.** A browser return alone never marks an order paid: the IPN or a QueryDR check must confirm it.
- **Card data.** ReelForge never sees card numbers: buyers pay on OnePAY's and payOS's hosted pages.
- **Testing.** Test with OnePAY's sandbox, then one small real payment, before accepting customers.

### Migrations

Each is applied by `python -m alembic upgrade head`, as usual:

- `0015_admin_payments_profiles`: display names and indexes for the paginated admin tables. Card orders need no other schema change.
- `0016_storage_lifecycle`: plan storage limits, asset kinds and expiry. Set up the storage root and the daily cleanup timer in section 11 when you upgrade.
- `0017_notify_support_verify`: notifications, support tickets and messages, and the live-verification checklist. It only adds tables; no existing row changes.
- `0018_admin_payment_config`: the encrypted payment gateway configuration and its audit trail. It only adds tables; orders, subscriptions, credits and the payment activity record are untouched, and legacy payment configuration keeps working.
- `0019_system_configuration`: the central system settings (plain and encrypted), their audit trail, and manual VietQR order history (`payment_order_events`, `payment_orders.transfer_reported_at`). It only adds; every existing row, setting, token and gateway configuration is untouched.
- `0020_manual_payment_statuses`: data only. Manual VietQR orders the buyer reported become `awaiting_confirmation`, and ones an admin rejected become `rejected`. Other orders are unchanged.

**After upgrading to Phase 18:**

- Restart the API, the frontend and every worker. Workers create the run, publishing and payment notifications, so a worker still on old code would stay silent.
- Optional variables, in `/etc/reelforge/runtime.env`:
  - `REELFORGE_SSE_POLL_SECONDS` (3);
  - `REELFORGE_SSE_MAX_SECONDS` (300);
  - `CREDITS_LOW_THRESHOLD` (20).
- Then open **Admin → Kiểm định**: readiness should show Migration ok. Work through the checklist ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)).

**After upgrading to Phase 21:**

1. Run `./deploy.sh`: it now refuses to restart services without a usable master key.
2. Optionally move the workers to the template unit (section 12).
3. Open Kiểm định → **Cấu hình**. It lists the settings that still come from `/etc/reelforge/runtime.env`. Save them in Cài đặt hệ thống until the list is empty.
4. Then remove the file: the units start without it.

**After upgrading to Phase 20:**

1. Run `./deploy.sh`: it applies the migration and restarts the API, frontend and workers.
2. Create the master key file from the existing key (section 4.1):

   ```bash
   sudo -u tai env REELFORGE_MASTER_KEY_FILE=/etc/reelforge/master.key .venv/bin/python -m app.master_key init
   ```

   Back the file up.
3. Edit the units once: change `EnvironmentFile=/etc/reelforge/runtime.env` to `EnvironmentFile=-/etc/reelforge/runtime.env`, then `sudo systemctl daemon-reload`.
4. Nothing else changes yet: every setting reads its old variable (source *Môi trường*).
5. Over time, enter the values in Quản trị → Cài đặt hệ thống. Once every setting shows *Admin* or *Mặc định*, `/etc/reelforge/runtime.env` can be emptied.
6. Remove `REELFORGE_TOKEN_ENCRYPTION_KEY` last, after confirming that Cài đặt hệ thống → Bảo mật reads the key from the file.

For manual VietQR (no gateway), see [PAYMENTS.md](PAYMENTS.md#vietqr-modes-phase-20).

**After upgrading to Phase 19:**

- Run `./deploy.sh`: it applies the migration and restarts the API and frontend.
- Check that **Kiểm định → Thanh toán** shows the encryption key as OK.
- Then move gateways to admin-managed configuration when convenient; nothing forces it.

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
curl -I https://reelforge.mul-service.com
```

---

## 17. Deploy script

The repository's `deploy.sh` is the deploy procedure. Run it as `tai` from the application directory:

```bash
cd ~/apps/reelforge-studio
./deploy.sh
```

**What it does:**

1. `git pull`.
2. `pip install -r requirements.txt`.
3. **Checks the bootstrap.**
   - `instance/bootstrap.json` (or `REELFORGE_DATABASE_URL`) must exist.
   - It must not set `frontend_origin` or `secure_cookies`: those are a development override, and the deploy stops.
   - `deploy/ensure-master-key.sh` makes sure there is a master key, before migrations and before any service is touched:
   - **A usable key** (the key file, or the legacy key the services still load from `/etc/reelforge/runtime.env`): continue.
     - **No key file, but the legacy key exists:** copy that same key into `/etc/reelforge/master.key`.
     - **No key at all:** check PostgreSQL (`python -m app.master_key encrypted`).
       - **Encrypted data exists** (OAuth tokens, payment or provider secrets): **STOP**, and ask for the old key file to be restored. A new key would make that data unreadable.
       - **The database cannot be read:** STOP.
       - **None (a new installation):** run `python -m app.master_key init`.
     - **Then:** verify with `python -m app.master_key status`, remind you to back the key up, and continue.

     `/etc/reelforge` belongs to root, so a new key is created as the service account in a private staging directory and installed with `sudo install` (owner `tai`, 600). Run `deploy.sh` as the account the services run as.
4. `alembic upgrade head`. On the first deploy after this change, `0021_default_production_origin` moves the old localhost defaults to `https://reelforge.mul-service.com` with Secure cookies. It keeps any other value.
5. `npm ci && npm run build`.
6. Restarts the API, the frontend and every enabled worker, in both forms (`reelforge-<name>-worker` and `reelforge-worker@<name>`).
7. Health checks on `http://127.0.0.1:8000` and `http://127.0.0.1:3001`.
   - Then the public origin from System Settings. If it does not answer, that is only a warning: check the tunnel.
8. Notes every unit in `deploy/systemd/` that differs from the installed copy (for example `reelforge-frontend.service`, now bound to 127.0.0.1). Review the change, copy it and run `daemon-reload` yourself.

It needs passwordless `sudo systemctl restart reelforge-*` for the user that runs it.

Configuration changes (keys, prices, storage, payment gateways) never need a deploy or a restart: save them in the admin UI.

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
- Keep Next.js bound to `127.0.0.1:3001` (`npm start -- --hostname 127.0.0.1`); expose it only through Cloudflare Tunnel.
- Do not expose the worker processes.
- Keep `frontend_origin` on the HTTPS production hostname (default `https://reelforge.mul-service.com`).
- Keep secure cookies on (the default).
- Keep `frontend_origin` / `secure_cookies` out of the server's `instance/bootstrap.json`.
- Keep provider, payOS, OnePAY, Google OAuth and Fernet secrets out of the repository.
- Back up PostgreSQL and the media root (`/srv/data/videos/reelforge`) together, and keep a copy off the HDD.
- Back up `/etc/reelforge/master.key` (or the legacy `REELFORGE_TOKEN_ENCRYPTION_KEY`) separately and securely: it protects OAuth tokens, payment credentials, AI keys and OAuth app secrets. Keep it `chmod 600`, owned by the service account.
- Apply Alembic migrations before restarting application services after an update.
- Review application and Cloudflare request-body/rate limits before public use.

---

## 21. Production endpoints

Expected production endpoints. Only the public frontend is HTTPS; everything else is private:

```text
Frontend:
https://reelforge.mul-service.com

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
 /srv/data/videos/reelforge
```

For external developer database access, use the separately configured PostgreSQL public endpoint only when required. Production ReelForge services on the home server should use the local PostgreSQL connection.

---

## 22. Accounts, email, backups and hardening (v1.0)

Version 1.0 adds account security, teams, observability and automated backups. On an existing installation, after
`./deploy.sh` (it applies migrations 0022–0025):

1. **Units.** Copy the changed and new units, then check that every service still starts with the hardening options:

   ```bash
   sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl restart reelforge-api reelforge-frontend 'reelforge-worker@*'
   systemctl --failed
   ```

2. **Backups.** Enable the daily database backup and run it once:

   ```bash
   sudo mkdir -p /srv/data/backups/reelforge && sudo chown tai:tai /srv/data/backups/reelforge && sudo chmod 700 /srv/data/backups/reelforge
   sudo systemctl enable --now reelforge-backup.timer
   sudo systemctl start reelforge-backup.service && journalctl -u reelforge-backup.service -n 20 --no-pager
   ```

   Then back the master key up **off the server** and tick the confirmation in **Quản trị → Cài đặt hệ thống → Sao
   lưu**. Restore checks and the rehearsal: [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md).
3. **Journal size:** `sudo mkdir -p /etc/systemd/journald.conf.d && sudo cp deploy/journald/reelforge.conf /etc/systemd/journald.conf.d/ && sudo systemctl restart systemd-journald`.
4. **Email.** In **Quản trị → Cài đặt hệ thống → Email**, set SMTP or Resend and press **Gửi email thử**. Password reset,
   email verification and emailed invitations need it ([EMAIL.md](EMAIL.md)).
5. **2FA** for every system administrator (Cài đặt → Bảo mật).
6. **Client addresses.** The defaults trust `CF-Connecting-IP` from loopback only, which matches this setup (Cloudflare
   Tunnel → Next.js on `127.0.0.1:3001` → API on `127.0.0.1:8000`). Keep both services on loopback
   ([SECURITY.md](SECURITY.md#the-client-address-and-its-trust-boundary)).
7. **Legal pages.** `/terms` and `/privacy` are templates: fill in every `[bracketed]` item and have them reviewed
   before launch. Existing accounts are asked to accept them at their next visit.
8. **Check.** `curl -fsS http://127.0.0.1:8000/health/ready`, Quản trị → Kiểm định, then the release checklist:
   [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md).

