# System configuration (Phases 20–21)

**Production ReelForge does not need a large `.env.runtime`.** The server needs two things before it can start:

| Bootstrap | Where | Why it cannot live in the database |
| --- | --- | --- |
| Database URL | `instance/bootstrap.json` → `{"database_url": "postgresql+psycopg://…"}`, or `REELFORGE_DATABASE_URL` when the file has none | It is how the application finds the database |
| Master encryption key | `/etc/reelforge/master.key` (chmod 600, owned by the service account) | It decrypts the secrets stored in the database; storing it there would defeat the encryption |

A new server is set up in ten steps: [PRODUCTION_BOOTSTRAP.md](PRODUCTION_BOOTSTRAP.md).

Everything else is configured by a system admin in the web UI and stored in PostgreSQL. Changes apply without SSH, without editing a file, and without restarting the API or workers:

- **Admin → Cài đặt hệ thống:**
  - AI provider keys;
  - OAuth apps;
  - storage;
  - runtime limits;
  - credit prices;
  - notification timing.
- **Admin → Thanh toán → Cổng thanh toán:** VietQR (manual or payOS) and cards (OnePAY).

## The public origin

**Topology.** Only the public origin is HTTPS:

```text
browser ── https://reelforge.mul-service.com ── Cloudflare Tunnel
        ── http://127.0.0.1:3001  Next.js (private; proxies /api/*)
        ── http://127.0.0.1:8000  FastAPI (private)
```

**System Settings → Chung** (table `system_settings`, not an environment variable). A new installation stores:

| Setting | Default |
| --- | --- |
| `frontend_origin` | `https://reelforge.mul-service.com` |
| `secure_cookies` | `true` |

Both stay editable. Secure cookies need an `https://` origin.

**What the origin decides:**

- **The same-origin check.** Every state-changing request, sign-in included, must carry this `Origin` (or the API's own). Anything else gets `403 Invalid origin`.
- **The session cookie** `rf_session`. It is always `HttpOnly` and `SameSite=Strict`, and `Secure` while `secure_cookies` is on. Logging out clears it with the same attributes.
- **Derived addresses:**
  - the OAuth redirect URLs, unless overridden;
  - the payment callback and return URLs shown to the admin.

**Upgrading.** Two data migrations replace **exact** old defaults only.

- `0021_default_production_origin` (history): if `frontend_origin` was exactly `"http://localhost:3000"`, it became
  the first production origin, `https://studio.imokome-cloud.com`, and `secure_cookies` became `true` if it was
  `false`. Downgrading it changes nothing.
- `0026_change_production_origin`: if `frontend_origin` is exactly `"https://studio.imokome-cloud.com"`, it becomes
  `https://reelforge.mul-service.com`, and `secure_cookies` becomes `true` if it was `false` (never the reverse).
  OAuth redirect overrides that are exactly that old origin's callbacks follow it. Downgrading returns only the exact
  new origin (and those callbacks) to the old one.
- Any other origin keeps every value, including:
  - another domain or port, a development origin such as `http://localhost:3000`;
  - an origin with a trailing slash or another scheme;
  - the new origin with cookies deliberately off.
- An OAuth redirect override that is not under the public origin (left on an old domain, for example) shows as a
  warning in Admin → Verification and in `deploy/release-preflight.sh`: update or clear it.

**Local development.** `next dev` runs on `http://localhost:3000`, where neither default works, so the development machine says so in its own `instance/bootstrap.json`:

```json
{"database_url": "…", "frontend_origin": "http://localhost:3000", "secure_cookies": false}
```

- **Scope.** These keys apply to the API on that machine only. They are never written to the database, so a development machine that shares the production database does not change the public setting.
- **Validation.** They are checked like the admin form: scheme and host only, and Secure cookies need `https://`. `secure_cookies` defaults to whether the origin is `https://`. An invalid value stops the API at start.
- **The admin page.** Cài đặt hệ thống → Chung shows a notice while an override is active. Saving the form there still stores the production values.
- **The server.** `deploy.sh` stops if the server's `instance/bootstrap.json` has either key.
- **The legacy file.** `instance/config.json` keeps its old meaning: its values are copied into System Settings once.

## The master key

`app/master_key.py` looks for the key in this order; the first match wins:

1. `REELFORGE_MASTER_KEY_FILE`: an explicit file. If this variable is set, the file must exist and be valid. There is no fallback, so a typo cannot silently switch keys.
2. `/etc/reelforge/master.key`: the production default.
3. `instance/master.key`: the development default (`instance/` is git-ignored).
4. `REELFORGE_TOKEN_ENCRYPTION_KEY`: the legacy variable, so existing installations keep working until they move the key into a file.

**What the key protects.** Every secret is encrypted with this key, or with a key derived from it by HKDF:

- OAuth tokens and upload sessions;
- payment gateway credentials;
- AI provider keys;
- OAuth app secrets.

A database backup alone reveals none of them.

```bash
python -m app.master_key status   # source, path, problems, permission and legacy warnings; never the key
python -m app.master_key init     # writes the key file (chmod 600; a new directory gets 700), then prints the status
```

**What `init` does:**

- **If `REELFORGE_TOKEN_ENCRYPTION_KEY` is set** (in the environment or the legacy runtime file), it **copies that key** into the file. Everything encrypted so far stays readable.
- **If no key exists and the database holds no encrypted data**, it generates a new key.
- **If the database already holds encrypted data**, it **refuses**: a new key would make that data unreadable. Restore the original key file instead.
- **If a key file already exists**, it changes nothing. It never overwrites a file, valid or not.

The application never generates a key by itself.

**Without the key:**

- the API still starts;
- Admin → Kiểm định and Admin → Cài đặt hệ thống → Bảo mật show the problem;
- saving any secret answers `key_missing`;
- stored secrets read as empty (providers report "key missing"). They are not deleted, and they never fall back to an environment variable.

Restoring the key file brings everything back.

**Hardening:**

- **Permissions.** The file is expected to be chmod 600, owned by the account the services run as. Readiness and `status` warn when other users can read it (POSIX only).
- **Logs.** The key's value is scrubbed from every log line, like environment secrets.
- **Reads.** The file is read again only when it changes.
- **Deploys.** `deploy.sh` runs `deploy/ensure-master-key.sh` before migrating or restarting anything. It keeps a usable key, and moves a legacy key into the file. It stops when the database already holds encrypted data and no key is found. It creates a key only on a new installation, then verifies it with `status`. `python -m app.master_key encrypted` answers "is there encrypted data?": exit 0 none, 1 yes, 2 cannot tell.

**Back up the key file separately from the database dumps**, for example on another machine or a password manager. Losing it means:

- re-entering every secret in the admin UI;
- reconnecting every YouTube, TikTok and Facebook channel.

Never change the key on a running installation; there is no key rotation.

## Where each setting comes from

`app/system_config.py` keeps one registry of settings, each plain (text, number, switch) or secret. Every process resolves a setting the same way:

1. **Admin:** the value a system admin saved (table `system_config`). Secrets are Fernet ciphertext there; plain values are JSON.
2. **Environment:** the legacy variable the setting replaces (table below), from the process environment or the optional `.env.runtime`.
3. **Default.**

**Rules:**

- A saved value always wins over the environment. Resetting a value, or clearing a secret, deletes the stored row, so the environment or the default applies again.
- A saved secret that cannot be decrypted is an error, never a silent fallback.
- An AI provider the admin switches **off** has no key, whatever the environment says.

The admin view shows each setting's source: **Admin**, **Môi trường (VARIABLE)**, **Mặc định**, or **Không đọc được**.

**Caching:**

- In the process that saves, a change applies to the next request.
- Other processes (every worker) cache settings for at most `CACHE_SECONDS` (15 s), then re-read the whole table in one query. No worker needs a restart.
- Secrets are cached decrypted in memory for that time only.

**Database reads are opt-in.** `start_process` turns them on: the API calls it when uvicorn starts (its lifespan), and every worker in `main`. Code that imports the API module without serving it, such as workers' helpers and unit tests, reads the environment exactly as before.

### Settings and the variables they replace

| Section | Setting | Legacy variable |
| --- | --- | --- |
| AI providers | OpenAI key, enabled | `OPENAI_API_KEY` |
|  | Anthropic key, enabled | `ANTHROPIC_API_KEY` |
|  | Gemini key, enabled | `GEMINI_API_KEY` |
|  | Runway secret, output hosts, enabled | `RUNWAYML_API_SECRET`, `RUNWAY_OUTPUT_HOSTS` |
|  | fal key, enabled | `FAL_KEY` |
|  | Runware key, enabled | `RUNWARE_API_KEY` |
|  | Replicate token, enabled | `REPLICATE_API_TOKEN` |
| Social OAuth | YouTube client ID, client secret, redirect override | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` |
|  | TikTok client key, client secret, redirect override, approved scopes | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`, `TIKTOK_APPROVED_SCOPES` |
|  | Facebook app ID, app secret, redirect override | `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`, `FACEBOOK_REDIRECT_URI` |
| Storage | Media root | `REELFORGE_STORAGE_ROOT` (then System Settings `storage_dir`) |
|  | Per-studio ceiling | `WORKSPACE_MEDIA_QUOTA_BYTES` |
|  | Retention: intermediate, temp, partial, orphan days | `REELFORGE_RETENTION_*_DAYS` |
| Runtime | ffmpeg, ffprobe, subtitle font, render timeout, still duration | `RENDER_FFMPEG_PATH`, `RENDER_FFPROBE_PATH`, `RENDER_SUBTITLE_FONT`, `RENDER_TIMEOUT_SECONDS`, `RENDER_STILL_SECONDS` |
|  | Video, image, voice job max wait; longest transcription | `VIDEO_JOB_MAX_AGE_SECONDS`, `IMAGE_JOB_MAX_AGE_SECONDS`, `VOICE_JOB_MAX_AGE_SECONDS`, `TRANSCRIPTION_MAX_SECONDS` |
| Credit pricing | Video clip, text, image, voice, render, transcription | `VIDEO_CREDITS_PER_CLIP`, `TEXT_CREDITS_PER_GENERATION`, `IMAGE_CREDITS_PER_GENERATION`, `VOICE_CREDITS_PER_GENERATION`, `RENDER_CREDITS_PER_JOB`, `TRANSCRIPTION_CREDITS_PER_JOB` |
| Notifications | Stream poll, stream lifetime, low-credit threshold | `REELFORGE_SSE_POLL_SECONDS`, `REELFORGE_SSE_MAX_SECONDS`, `CREDITS_LOW_THRESHOLD` |
| Credit pricing | Visual analysis, per batch of 10 frames (movie sources) | `VISION_CREDITS_PER_BATCH` |
| Movie sources | Enabled; retention, longest retention, delete after success and its grace; largest and longest movie; import folder, scratch space, local copies; frame interval and count; Google Drive (enabled, OAuth or service account, root folder, client ID, client secret, refresh token, service-account key, delete mode, warning size) | — (new; [MOVIE_SOURCES.md](MOVIE_SOURCES.md)). The Drive secrets are encrypted and write-only |
| Payments (Admin → Thanh toán) | VietQR mode, manual bank QR details | — (new) |
| Payments (Admin → Thanh toán) | payOS, OnePAY | `payos` in `instance/bootstrap.json`, `ONEPAY_*` (Phase 19, table `payment_provider_configs`) |

Numbers are validated against the same ranges the code enforces, for example:

- credits per clip: 1–100,000;
- render timeout: 60–21,600 s;
- still duration: 1–60 s.

Credit prices are ReelForge's internal credits per operation, not money paid to providers.

### Every environment variable, and why it remains

`app/system_config.py` lists every variable the backend still reads (`ENVIRONMENT`, plus the registry's legacy names). `tests/test_phase21.py` fails if code reads a variable that is not classified. Only the modules in the table below may read `os.environ` directly; everything else asks `system_config`.

**A. Bootstrap: allowed outside the database**

| Variable | Read by | Why |
| --- | --- | --- |
| `REELFORGE_DATABASE_URL` (optional) | `app/db.py` | The database URL when `instance/bootstrap.json` has none. The file wins |
| `REELFORGE_MASTER_KEY_FILE` (optional) | `app/master_key.py` | The key file when not at `/etc/reelforge/master.key` |
| `REELFORGE_LOG_FORMAT`, `REELFORGE_LOG_LEVEL` (optional) | `app/logs.py` | Read once when a process configures logging, before the database is opened |

**B. Legacy fallback only.** A value saved in the admin UI always wins over these:

| Variable | Read by | Replaced by |
| --- | --- | --- |
| Every variable in the settings table above | `system_config.env` | Admin → Cài đặt hệ thống |
| `REELFORGE_TOKEN_ENCRYPTION_KEY` | `app/master_key.py` | The key file (`python -m app.master_key init` copies it) |
| `ONEPAY_*` | `app/payment_config.py`, `onepay.py` | Admin → Thanh toán → Cổng thanh toán → Thẻ |
| `payos` in `instance/bootstrap.json` | `app/payment_config.py` | Admin → Thanh toán → Cổng thanh toán → VietQR |
| `REELFORGE_ENV_FILE` | `app/runtime_env.py` | Names a legacy runtime file; none is needed |

**C. Should move to the admin UI.** None remain: every normal provider credential and runtime setting is in the registry.

**D. Experimental, development and tests:**

| Variable | Read by | Why |
| --- | --- | --- |
| `DOLA_*` | `app/providers/dola.py`, `catalog.py` | The experimental Dola gateway, off unless an operator runs one |
| `REELFORGE_SMOKE_*`, `REELFORGE_LIVE_TESTS` | `app/provider_check.py`, `app/smoke_test.py` | Live smoke-test choices; deliberately not in the production UI. The tools themselves read keys from the database when run on a server |
| `REELFORGE_TEST_DATABASE_URL` | tests | Runs the migration tests on an isolated PostgreSQL |
| `REELFORGE_GOOGLE_API_BASE` | `app/google_drive.py` | Points the Google Drive client at a local test server (loopback addresses only; tests and the browser suite) |

Admin → Cài đặt hệ thống → Bảo mật lists the known variables set in the API's environment: names and categories only, never values. It also lists the settings that still come from a legacy variable.

## Hot configuration: workers and the API

Every worker reads its settings per job, never once at start-up:

- `text_worker`, `image_worker`, `video_worker`, `voice_worker`, `source_worker`, `render_worker`, `youtube_worker`, `social_worker`, `scheduler_worker`, `movie_worker`;
- the settings they read: provider keys, credit prices, job limits, FFmpeg, storage, OAuth apps.

**Timing:**

| Where | Delay |
| --- | --- |
| In the process that saved | Applies to the next request |
| Every other process | Within `CACHE_SECONDS` = **15 s**: settings are cached per process and re-read in one query |
| Payment gateways (`payment_provider_configs`) | Not cached: read per request |
| Master key file | Re-read when it changes (size or modification time) |

**Proof.** `tests/test_phase21.py` starts a separate worker process, then changes the OpenAI key, the VietQR mode and the card switch through the API. The running process sees all three without a restart.

**Readiness.** Admin → Kiểm định → **Cấu hình** shows:

- how many settings still come from the environment;
- whether a legacy runtime file was loaded;
- the cache time.

A fresh installation shows none of the first two.

## The admin UI

**Admin → Cài đặt hệ thống** has ten sections. It is system-admin only, enforced by the API (`/api/admin/system-config…`).

- **Security:** where the master key comes from (file, legacy variable, missing), its path, whether the file is chmod 600, and whether a legacy variable still set differs from the file. The key itself is never shown or editable here. Since v1.0 also the client address behind the proxy: `security.trusted_proxies` (CIDR list, default `127.0.0.0/8,::1/128`) and `security.client_ip_header` (default `CF-Connecting-IP`; empty trusts no header). `X-Forwarded-For` is never used ([SECURITY.md](SECURITY.md#the-client-address-and-its-trust-boundary)).
- **General:** frontend origin, secure cookies, trial project limit, self-registration (the same settings as before, moved here).
- **AI providers:** one card per provider with a switch, the write-only key, Runway's output hosts, how many enabled models use it, and **Kiểm tra kết nối**. The test sends one authenticated request to a free listing endpoint (models or account): OpenAI, Anthropic, Gemini, Replicate, Runway. It never generates anything; FAL and Runware get a local check only.
- **Social OAuth:** YouTube, TikTok and Facebook app credentials. The redirect URL in use is shown with a copy button: derived from the frontend origin (`/youtube/callback`, `/channels/callback/tiktok`, `/channels/callback/facebook`) unless overridden. Changing an app never exposes stored tokens, which stay encrypted with the master key. A channel may need reconnecting if the new app is a different one.
- **Storage:** the media root and the limits.
  - The root must be an absolute path to an existing, writable directory that is not a link or junction.
  - Changing it while files exist asks for confirmation. **Files are never moved:** move them yourself, or with a migration tool, before or after.
  - The per-studio ceiling is entered in GB; the retention periods in days.
- **Email (v1.0):** enabled, provider (`smtp` or `resend`), from name and address, reply-to, SMTP host, port, security, username and password, Resend API key, and **Gửi email thử**. The password and the key are secrets ([EMAIL.md](EMAIL.md)).
- **Backups (v1.0):** `backups.directory` (default `/srv/data/backups/reelforge`), `backups.keep_daily` / `keep_weekly` / `keep_monthly` (14 / 8 / 6), `backups.max_age_hours` (26: older is an alert), the last runs, and the master key backup confirmation (a fingerprint only) ([BACKUP_RECOVERY.md](BACKUP_RECOVERY.md)).
- **Runtime:** FFmpeg paths, subtitle font, render timeout, still duration, job waits.
- **Credit pricing:** the six per-operation credit prices.
- **Notifications:** stream timing and the low-credit threshold.

**Secrets are write-only:**

- a saved secret shows as `••••` only;
- leaving the field blank keeps it;
- typing replaces it;
- the eraser removes the stored value, and the environment variable applies again, if set.

**Saving:**

- Saving one card changes only that card's settings.
- Every save is recorded in `system_config_audit` with the setting **names** only, never a value.
- Each AI connection test is recorded with its statuses.

### API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/admin/system-config` | Every section: per setting `source`, `value` (plain only) or `configured` (secrets), range, default, who changed it and when. Also the redirect URLs, models per provider, master key status and storage summary |
| `PUT /api/admin/system-config/{section}` | `{values: {key: value}, secrets: {key: {action: keep\|replace\|clear, value}}, reset: [keys], confirm_root_change}`. Validated as a whole; 422 `{code, field}` never echoes a value. A root change while files exist answers 409 `root_change_requires_confirmation` |
| `POST /api/admin/system-config/ai/{provider}/test` | Local check, then one free listing request (`{local, remote}`) |
| `POST /api/admin/system-config/storage/check` | `{root}` → whether it is usable and whether it would move away from the current root |
| `POST /api/admin/system-config/email/test` | `{to?}` → sends the test email (10 per hour); `{ok, error}` with an error code, never a server response |
| `PUT /api/admin/master-key/backup-confirmation` | `{confirmed}`: records that the key is backed up off the server (its fingerprint, the time, the admin) |

## Upgrading an existing installation

Since `0021_default_production_origin` and `0026_change_production_origin`, an installation still on the old localhost defaults, or on the first production origin, moves to the public origin ([above](#the-public-origin)). A development machine adds its override to `instance/bootstrap.json` first.

1. Apply the migration: `python -m alembic upgrade head` (`0019_system_configuration`). It only adds tables and one nullable column.
2. Move the key into a file, as the service account:

   ```bash
   sudo -u reelforge env REELFORGE_MASTER_KEY_FILE=/etc/reelforge/master.key \
     /path/to/.venv/bin/python -m app.master_key init
   ```

   This copies `REELFORGE_TOKEN_ENCRYPTION_KEY` from the runtime file. Check that the file is mode 600, then back it up.
3. Restart the services once. Nothing changes yet: every setting still reads its environment variable (source **Môi trường**).
4. At your own pace, enter the values in Admin → Cài đặt hệ thống. Each saved value wins over the environment at once.
5. When every setting shows **Admin** or **Mặc định**, the runtime file can shrink to nothing.
   - Keep `REELFORGE_TOKEN_ENCRYPTION_KEY` until the key file is in place and backed up.
   - Then remove it; Bảo mật warns if a remaining legacy variable differs from the file.
