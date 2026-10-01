# System configuration (Phase 20)

**Production ReelForge does not need a large `.env.runtime`.** The server needs two things before it can start:

| Bootstrap | Where | Why it cannot live in the database |
| --- | --- | --- |
| Database URL | `instance/bootstrap.json` → `{"database_url": "postgresql+psycopg://…"}` | It is how the application finds the database |
| Master encryption key | `/etc/reelforge/master.key` (chmod 600, owned by the service account) | It decrypts the secrets stored in the database; storing it there would defeat the encryption |

Everything else is configured by a system admin in the web UI and stored in PostgreSQL. Changes apply without SSH, without editing a file, and without restarting the API or workers:

- **Admin → Cài đặt hệ thống:**
  - AI provider keys;
  - OAuth apps;
  - storage;
  - runtime limits;
  - credit prices;
  - notification timing.
- **Admin → Thanh toán → Cổng thanh toán:** VietQR (manual or payOS) and cards (OnePAY).

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
python -m app.master_key status   # where the key comes from and what is wrong; never prints the key
python -m app.master_key init     # writes the key file with chmod 600 (see below)
```

**What `init` does:**

- **If `REELFORGE_TOKEN_ENCRYPTION_KEY` is set** (in the environment or the legacy runtime file), it **copies that key** into the file. Everything encrypted so far stays readable.
- **If no key exists and the database holds no encrypted data**, it generates a new key.
- **If the database already holds encrypted data**, it **refuses**: a new key would make that data unreadable. Restore the original key file instead.
- **If a key file already exists**, it changes nothing.

The application never generates a key by itself.

**Without the key:**

- the API still starts;
- Admin → Kiểm định and Admin → Cài đặt hệ thống → Bảo mật show the problem;
- saving any secret answers `key_missing`;
- stored secrets read as empty (providers report "key missing"). They are not deleted, and they never fall back to an environment variable.

Restoring the key file brings everything back.

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
| Payments (Admin → Thanh toán) | VietQR mode, manual bank QR details | — (new) |
| Payments (Admin → Thanh toán) | payOS, OnePAY | `payos` in `instance/bootstrap.json`, `ONEPAY_*` (Phase 19, table `payment_provider_configs`) |

Numbers are validated against the same ranges the code enforces, for example:

- credits per clip: 1–100,000;
- render timeout: 60–21,600 s;
- still duration: 1–60 s.

Credit prices are ReelForge's internal credits per operation, not money paid to providers.

### What still comes from the environment, and why

| Variable | Why |
| --- | --- |
| `REELFORGE_MASTER_KEY_FILE` (optional) | Bootstrap: where the key file is when not at the default path |
| `REELFORGE_LOG_FORMAT`, `REELFORGE_LOG_LEVEL` (optional) | Read once when a process configures logging, before it opens the database; changing them at runtime would add complexity for no operator benefit |
| `REELFORGE_SMOKE_*`, `REELFORGE_LIVE_TESTS`, `REELFORGE_LIVE_VIDEO` | Development and live-test tooling (`python -m app.smoke_test …`); deliberately not in the production UI |
| `DOLA_*` | The experimental Dola gateway, off unless an operator runs one |
| `REELFORGE_TEST_DATABASE_URL` | Tests only |
| `REELFORGE_ENV_FILE` | Names a legacy runtime file, if one is still used |

## The admin UI

**Admin → Cài đặt hệ thống** has eight sections. It is system-admin only, enforced by the API (`/api/admin/system-config…`).

- **Security:** where the master key comes from (file, legacy variable, missing), its path, whether the file is chmod 600, and whether a legacy variable still set differs from the file. The key itself is never shown or editable here.
- **General:** frontend origin, secure cookies, trial project limit, self-registration (the same settings as before, moved here).
- **AI providers:** one card per provider with a switch, the write-only key, Runway's output hosts, how many enabled models use it, and **Kiểm tra kết nối**. The test sends one authenticated request to a free listing endpoint (models or account): OpenAI, Anthropic, Gemini, Replicate, Runway. It never generates anything; FAL and Runware get a local check only.
- **Social OAuth:** YouTube, TikTok and Facebook app credentials. The redirect URL in use is shown with a copy button: derived from the frontend origin (`/youtube/callback`, `/channels/callback/tiktok`, `/channels/callback/facebook`) unless overridden. Changing an app never exposes stored tokens, which stay encrypted with the master key. A channel may need reconnecting if the new app is a different one.
- **Storage:** the media root and the limits.
  - The root must be an absolute path to an existing, writable directory that is not a link or junction.
  - Changing it while files exist asks for confirmation. **Files are never moved:** move them yourself, or with a migration tool, before or after.
  - The per-studio ceiling is entered in GB; the retention periods in days.
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

## Upgrading an existing installation

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
