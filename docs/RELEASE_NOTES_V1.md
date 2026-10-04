# ReelForge Studio v1.0.0 — release notes

> **Not released yet: release candidate.** These notes describe what v1.0 ships. The tag `v1.0.0` is created only
> after every release gate passes on the production server ([V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md)).
> Integrations with outside services (AI providers, payment gateways, social platforms, email) are implemented and
> tested against fakes in CI; each one is verified live on the production server as a release gate, and nothing below
> claims that a gate has passed.

Production origin: `https://reelforge.mul-service.com` (Cloudflare Tunnel to a home server). Database head:
`0027_movie_sources`. Interface in Vietnamese (default), English and Japanese.

## Creating content

- **Workflow engine:** workflows as graphs of steps with typed connections and per-step settings; each run freezes
  its graph and settings, records every step, can wait for a person's review and be retried. 54 steps in the library,
  all executable. Templates: Social video, YouTube Short, YouTube (16:9), TikTok video, Facebook Reel, Repurpose
  existing content, Movie Recap, Movie Review, Movie Review and Movie Recap from a movie source, Article → Video,
  Product Video, Blank.
- **AI generation:** text (OpenAI, Anthropic, Gemini), images (Runway), video clips, one per scene (fal, Runware,
  Replicate, Runway; Dola as an opt-in experiment), narration (Gemini TTS), transcription (OpenAI Whisper). Credits are
  reserved before a provider call, charged once when the result is stored and refunded on a clear failure; an
  uncertain outcome holds the credits for an administrator's reconciliation.
- **Sources and repurposing:** text, a public web page (fetched safely), uploaded documents, audio and video, with
  timestamped transcripts.
- **Movie Recap / Review:** transcript, story analysis, recap or review script, the matching source scenes cut with
  FFmpeg and rendered with the narration, for material the user is authorized to use.
- **Movie sources** (added after the release closure): temporary movies from the studio's server import folder, a
  direct https URL or the studio's Google Drive inbox, kept in the operator's Google Drive with retention and deleted
  automatically after use, never while a run uses them; *Use for Movie Review / Recap* runs a pipeline with grounded
  visual analysis of sampled frames, a timeline, time-ranged sections and short excerpts.
  [MOVIE_SOURCES.md](MOVIE_SOURCES.md)
- **Rendering:** FFmpeg renders one H.264/AAC MP4 from the clips or an image slideshow, with narration, burned-in
  subtitles (SRT/WebVTT) and background music.

## Publishing

- **Channels:** YouTube (private by default), TikTok (inbox drafts) and Facebook Page Reels, each through its official
  OAuth and upload API; one approved video to several channels, each with its own metadata and upload job. An uncertain
  upload is marked for checking, never reported as a success.
- **Scheduling:** schedule, move or cancel a publication until its upload starts; the Calendar shows every channel.

## Studios, billing and teams

- **Billing and payments:** plans with project, workflow and storage limits and monthly credits; an append-only credit
  ledger; VietQR / Bank Transfer (manual, confirmed by an administrator, or payOS) and Credit / Debit Card (OnePAY).
  One settlement path: the exact amount, the order's own provider, credits and the subscription once, duplicate and
  late callbacks harmless.
- **Teams and workspaces:** owner, admin, editor and viewer roles enforced by the API; email invitations; a studio
  switcher; ownership transfer; immediate removal; records of another studio answer 404.
- **Home (user dashboard):** the studio's figures, what needs the member's attention, quick templates and recent
  work, AI usage and publishing, within the member's role.
- **Storage lifecycle:** one media root, plan quotas with warnings, retention of intermediate files, a daily cleanup
  that deletes only verified files.
- **Notifications and support:** a live notification bell (Server-Sent Events, polling as a fallback) and in-app
  support requests with replies.

## Accounts and security

Email verification, forgot and reset password, change password, optional TOTP two-factor authentication with recovery
codes, sessions that can be signed out, account data export and closure request, Terms and Privacy acceptance. Durable
rate limits, the real client address behind Cloudflare only from trusted proxies, same-origin checks, security
headers, an audit log, the first administrator created only on the server, and a break-glass recovery command.
Transactional email through SMTP or Resend, 15 templates in three languages, delivered from an outbox.

## Administration and operations

- **Admin dashboard (Overview):** the first of eleven admin tabs: system figures, what needs attention, health, AI
  usage, payments, credits, growth, publishing, storage, support and recent activity, each opening the tab that
  handles it. The other tabs: Users, Studios & credits, Plans, Payments, Support, Credit reconciliation, Operations,
  Verification, System settings, Audit log.
- **System administration:** every setting in Admin → System settings and Admin → Payments, stored in PostgreSQL with
  secrets encrypted by the master key; production needs only the database URL and the key file.
- **Backup and recovery:** a daily `pg_dump` timer with checked archives and retention, restore checks and a recovery
  rehearsal into a scratch database, media manifests, the master key backed up apart from the dumps.
- **Observability:** `/health/live`, `/health/ready`, Prometheus metrics on `127.0.0.1`, alerts with a cooldown, JSON
  logs with request IDs.
- **Production deployment:** hardened systemd units, `./deploy.sh` (pull, bootstrap and master key checks,
  migrations, build, restarts, service and readiness checks), the release pre-flight and the release report, 75 release
  gates recorded in Admin → Verification (seven of them, optional, for movie sources). Domain `https://reelforge.mul-service.com` (migration
  `0026_change_production_origin` moves an installation from the previous domain).

## Verified

- **Automatically, in CI** (GitHub Actions on the audited commit `56ada10`, all green): backend tests on SQLite (Python
  3.11 and 3.14) and PostgreSQL 16, migrations up, down and up with `alembic check`, the frontend build on Node 20 and
  22, and the browser suite on PostgreSQL, layout at seven window sizes included. Counts from the release closure:
  [RELEASE_V1_CHECKLIST.md](RELEASE_V1_CHECKLIST.md#automated-evidence-development-machine-2026-10-03-release-closure).
  The movie source phase came after that commit: so far only local runs of the same suites
  ([2026-10-04](RELEASE_V1_CHECKLIST.md#automated-evidence-development-machine-2026-10-04-movie-source-phase)); CI
  must still pass on its commit.
- **Live, on the production server:** the release gates (email, AI providers, render, payments, publishing,
  Cloudflare and security, backups, reboot, legal) are recorded by the operator in Admin → Verification; their state
  is in [V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md). During development the operator ran Gemini text and Runway
  `gen4.5` / `gen4_image` for real; on the production server they are gates like the others.

## Before going live

- The Terms of Service and the Privacy Policy are templates with placeholders until the operator fills them in and a
  lawyer reviews them (`legal_terms_reviewed`, `legal_privacy_reviewed`).
- Outside systems that hold the public address are updated by hand after the domain change (Cloudflare, Google,
  TikTok, Meta, payOS, OnePAY).

## Known limitations

No master-key rotation, single sign-on or studio deletion; account closure handled by an administrator; expired
sessions not purged; alerts in the app only; one API process by default; credits not tied to the providers' money
cost; one local media root. [FINAL_PRODUCT_AUDIT.md § 16](FINAL_PRODUCT_AUDIT.md#16-known-limitations); what may come
next: [POST_V1_ROADMAP.md](POST_V1_ROADMAP.md).

## Upgrading

Back up the database, then `./deploy.sh` on the server: it migrates to `0027_movie_sources`, builds,
restarts and checks every service. Then `bash deploy/release-preflight.sh` and the domain change steps in
[V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md#domain-change).
