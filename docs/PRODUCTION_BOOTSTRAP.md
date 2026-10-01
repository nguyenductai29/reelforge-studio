# Production bootstrap: a new server from zero

ReelForge needs exactly two things outside its database:

| Bootstrap | Where | Created by |
| --- | --- | --- |
| The database URL | `instance/bootstrap.json` → `{"database_url": "postgresql+psycopg://…"}` | You, once |
| The master encryption key | `/etc/reelforge/master.key` (chmod 600, owned by the service account) | `python -m app.master_key init`, once |

Everything else is configured in the web UI and stored in PostgreSQL, with secrets encrypted. It applies without SSH or restarts:

- **Quản trị → Cài đặt hệ thống:** AI keys, OAuth apps, storage, email, security (trusted proxy), backups, runtime, credits, notifications.
- **Quản trị → Thanh toán → Cổng thanh toán:** VietQR, cards.

**The public origin is `https://studio.imokome-cloud.com`, and only it is HTTPS.**

| Service | Address | Reachable from |
| --- | --- | --- |
| Public origin | `https://studio.imokome-cloud.com` | The internet, through the Cloudflare Tunnel |
| Frontend service (Next.js) | `http://127.0.0.1:3001` | This server only |
| API (FastAPI) | `http://127.0.0.1:8000` | This server only; Next.js proxies `/api/*` |

A new installation stores this origin with Secure cookies on (Cài đặt hệ thống → Chung), so sign-in works through the tunnel without changing anything.

**No `.env.runtime` is needed.** The details are in [SYSTEM_CONFIGURATION.md](SYSTEM_CONFIGURATION.md); the full home-server walkthrough is in [home-server-deployment.md](home-server-deployment.md).

## Disk layout (home server)

| Disk | Holds |
| --- | --- |
| SSD | OS, the application (`/home/tai/apps/reelforge-studio`), its virtualenv, PostgreSQL, `/etc/reelforge` |
| HDD at `/srv/data` | Media and backups |

```
/srv/data/
├── videos/reelforge/      media root (set in Admin → Cài đặt hệ thống → Lưu trữ)
└── backups/reelforge/     daily pg_dump (reelforge-backup.timer), chmod 700
```

A backup on the same HDD does **not** survive that disk failing. Copy the dumps and the key file to another machine too.

## Steps

1. **System packages:**

   ```bash
   sudo apt install -y python3-venv ffmpeg fonts-noto-core fonts-noto-cjk
   ```

   Also install Node.js 20+ and PostgreSQL.
2. **Source and dependencies:**

   ```bash
   git clone -b feat/studio-foundation https://github.com/nguyenductai29/reelforge-studio ~/apps/reelforge-studio
   cd ~/apps/reelforge-studio
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```

3. **The database URL.** Create the PostgreSQL role and database, then:

   ```bash
   mkdir -p instance
   printf '{"database_url": "postgresql://USER:PASSWORD@127.0.0.1:5432/reelforge_studio_db"}\n' > instance/bootstrap.json
   chmod 600 instance/bootstrap.json
   .venv/bin/python -m alembic upgrade head
   ```

   `REELFORGE_DATABASE_URL` can replace the file in container-style setups. The file wins when both exist.

   On the server the file holds `database_url` (and nothing about the origin). `frontend_origin` / `secure_cookies` in it are a development machine's override: `deploy.sh` stops if it finds them.
4. **The master key, as the service account:**

   ```bash
   sudo mkdir -p /etc/reelforge && sudo chown root:tai /etc/reelforge && sudo chmod 750 /etc/reelforge
   sudo -u tai .venv/bin/python -m app.master_key init      # creates /etc/reelforge/master.key, chmod 600
   .venv/bin/python -m app.master_key status                 # never prints the key
   ```

   **Back the key file up off the server now**, separately from the database backups. Without it, every stored secret is lost. `init` never overwrites a file, and never generates a key over an installation that already holds encrypted data.
5. **Media folders on the HDD:**

   ```bash
   sudo mkdir -p /srv/data/videos/reelforge /srv/data/backups/reelforge
   sudo chown -R tai:tai /srv/data/videos/reelforge /srv/data/backups/reelforge
   ```

6. **Frontend:**

   ```bash
   printf '{"api_base_url": "http://127.0.0.1:8000"}\n' > frontend/instance/config.json
   (cd frontend && npm ci && npm run build)
   ```

7. **Services** (`deploy/systemd/`; adjust `User` and paths if they differ):

   ```bash
   sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now reelforge-api reelforge-frontend
   sudo systemctl enable --now reelforge-worker@{text,image,video,voice,render,source,youtube,social,scheduler}
   sudo systemctl enable --now reelforge-media-maintenance.timer
   sudo systemctl enable --now reelforge-backup.timer        # daily database backup, 02:30
   sudo mkdir -p /etc/systemd/journald.conf.d && sudo cp deploy/journald/reelforge.conf /etc/systemd/journald.conf.d/
   sudo systemctl restart systemd-journald                   # journal size limits
   ```

   No unit sets a provider key.
   - Every unit is hardened (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`, kernel protections) without blocking
     `/home/tai/apps`, `/srv/data` or `/etc/reelforge/master.key`; check each service once after installing
     ([BACKUP_RECOVERY.md](BACKUP_RECOVERY.md#systemd-hardening)).
   - `EnvironmentFile=-/etc/reelforge/runtime.env` is optional; leave the file out.
   - `/etc/reelforge/master.key` is the default path, so the units need no variable for it.
   - The API listens on `127.0.0.1:8000` and the frontend on `127.0.0.1:3001` (`npm start -- --hostname 127.0.0.1`). Neither is reachable from the network.
8. **Public access:** a Cloudflare Tunnel route `studio.imokome-cloud.com` → `http://127.0.0.1:3001` (home-server-deployment.md § 14). Cloudflare terminates HTTPS.
9. **In the browser:**
   1. Create the first admin (accepting the Terms and Privacy Policy). Then turn on two-factor authentication for it
      (**Cài đặt → Bảo mật**) and store its recovery codes.
   2. In **Cài đặt hệ thống**:
      - **Chung:** check the public frontend origin, `https://studio.imokome-cloud.com`, with secure cookies on (the defaults). Change it only for another hostname.
      - **Email:** SMTP or Resend, the sender address; press **Gửi email thử** ([EMAIL.md](EMAIL.md)). Until email works,
        "Forgot password" is hidden and invitations show a link to copy instead.
      - **Bảo mật:** the trusted proxies (`127.0.0.1`/`::1`, the default) and the client address header
        (`CF-Connecting-IP`, the default). See [SECURITY.md](SECURITY.md).
      - **Sao lưu:** the backup folder and retention; tick that the master key is backed up off the server.
      - **Lưu trữ:** `/srv/data/videos/reelforge`.
      - **Nhà cung cấp AI:** the keys you use. Press **Kiểm tra kết nối** for each.
      - **OAuth mạng xã hội:** the apps, with the redirect URLs registered as shown.
   3. In **Thanh toán → Cổng thanh toán**:
      - VietQR: manual (bank account) or payOS;
      - Card: OnePAY, sandbox first.
   4. In **Cấu hình gói:** the plan prices.
10. **Check:** `curl -fsS http://127.0.0.1:8000/health/ready`, then open **Kiểm định**. Every readiness section should be green (backups, email and the master key backup included). **Cấu hình** should read "no setting from the environment" and no runtime file. Then work through the checklist ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md)) and the release checklist ([RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md)), including a recovery rehearsal ([BACKUP_RECOVERY.md](BACKUP_RECOVERY.md)).

## Updates

`./deploy.sh` does the following:

1. pulls the source and installs the dependencies;
2. stops if `instance/bootstrap.json` sets `frontend_origin` or `secure_cookies` (a development override);
3. makes sure there is a master key (`deploy/ensure-master-key.sh`):
   - **A usable key** (the key file, or the legacy key the services still load from `/etc/reelforge/runtime.env`): continue.
   - **No key file, but the legacy key exists:** copy that same key into `/etc/reelforge/master.key`.
   - **No key at all:** check PostgreSQL (`python -m app.master_key encrypted`).
     - **Encrypted data exists** (OAuth tokens, payment or provider secrets): **STOP**, and ask for the old key file to be restored. A new key would make that data unreadable.
     - **The database cannot be read:** STOP.
     - **None (a new installation):** run `python -m app.master_key init`.
   - **Then:** verify with `python -m app.master_key status`, remind you to back the key up, and continue.

   `/etc/reelforge` belongs to root, so a new key is created as the service account in a private staging directory and installed with `sudo install` (owner `tai`, 600). Run `deploy.sh` as the account the services run as.
4. migrates and builds;
5. restarts the API, the frontend and every enabled worker (`reelforge-<name>-worker` or `reelforge-worker@<name>`);
6. waits up to 30 s for `http://127.0.0.1:8000/health/ready` (database, migrations at head, master key usable; no paid provider is called), then checks `http://127.0.0.1:3001`;
7. checks the public origin stored in System Settings. A failure there is a warning only, since the tunnel runs on its own;
8. notes every unit in `deploy/systemd/` that differs from the installed copy or is not installed yet, and reminds you when the backup timer is not enabled.

Configuration changes never need a deploy: save them in the admin UI.

Migration `0021_default_production_origin` moves an installation still on the old defaults (`http://localhost:3000`, cookies not Secure) to `https://studio.imokome-cloud.com` with Secure cookies. It keeps any other value an admin saved.

## Backups

The database is dumped daily by `reelforge-backup.timer` (`python -m app.backup run`: `pg_dump` custom format,
checked, chmod 600, rotated 14 daily / 8 weekly / 6 monthly; the newest dump is never deleted). Admin → Verification
shows the last success and its age, and an alert is raised when it is overdue or fails.

**Keep off the server:**

- copies of the dumps;
- `/etc/reelforge/master.key`, **separately from the dumps** (the timer never copies it);
- `instance/bootstrap.json`.

A dump alone reveals no secret, and a dump without its key cannot use them. Restore checks, the recovery rehearsal,
media backups and the full restore procedure: [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md).
