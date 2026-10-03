# ReelForge Studio — after v1.0

Candidates for releases after v1.0.0. **None of them is a v1.0 release blocker** and none is being built now: each
needs its own design, migration (if any), tests and release. The v1.0 release itself is decided only by
[V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md); the limitations these items address are listed in
[FINAL_PRODUCT_AUDIT.md § 16](FINAL_PRODUCT_AUDIT.md#16-known-limitations).

## Security and accounts

| Item | Today (v1.0) |
| --- | --- |
| Master-key rotation (re-encrypt every stored secret under a new key) | One key in `/etc/reelforge/master.key`; changing it makes saved secrets unreadable, so they are entered again |
| Cleanup job for expired sessions and used tokens | They stay in their tables and are ignored everywhere |
| Single sign-on (OIDC / SAML) | Email and password, with optional TOTP 2FA |
| Account deletion and anonymization, automated | A closure request reaches Support; an administrator handles it by hand ([LIVE_VERIFICATION.md](LIVE_VERIFICATION.md#support-and-account-closure)) |
| Studio (workspace) deletion with its subscription, credits, media and publications settled | Not offered; an administrator can deactivate accounts |
| `__Host-` prefix for the session cookie | `rf_session` is `HttpOnly`, `Secure`, `SameSite=Strict` (audit finding 19, LOW) |
| A per-studio audit view | The audit log is for system administrators |
| Next.js 16 (its bundled PostCSS has published advisories; no Next.js 15 release fixes them) | Next.js 15.5; the advisories need attacker-written CSS, which the build never compiles ([V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md#remaining-blockers)) |

## Operations and scale

| Item | Today (v1.0) |
| --- | --- |
| Alerts delivered outside the app (email, chat, push) | In-app notifications to system administrators |
| Several API processes or servers: notifications through a pub/sub channel, shared caches | One API process by default; each notification stream polls the database ([LOAD_BASELINE.md](LOAD_BASELINE.md)) |
| Rate limits configurable in System settings | Fixed in code |
| Provider cost accounting (what each step cost in money) | Credits per step from the configured prices; not tied to the providers' invoices |

## Storage and backups

| Item | Today (v1.0) |
| --- | --- |
| Object storage (S3-compatible) for media | One local media root (`/srv/data/videos/reelforge` on the server) |
| Scheduled off-server copies of media and dumps | Copied by the operator (`rsync`, `restic`), checked with the media manifest ([BACKUP_RECOVERY.md](BACKUP_RECOVERY.md)) |

## Payments and content

| Item | Today (v1.0) |
| --- | --- |
| Automatic renewal; refunds in the app; separate sandbox and production profiles per gateway | Renewal by the owner; refunds outside the app; one configuration per gateway |
| Bank statement matching for manual VietQR | An administrator confirms each transfer |
| Audio ducking and fades; motion for still images | One background track; stills without motion |
| Instagram publishing | Hidden: needs a professional account, its own publish flow and Meta review |
