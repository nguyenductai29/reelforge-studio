# Movie sources: automatic Movie Review, Movie Recap and Ending Explained

A **movie source** is a temporary copy of a movie that a studio turns into a narrated review, recap or ending
explanation. The movie is kept in the operator's Google Drive while a workflow needs it, downloaded once to the
server's scratch space for a review, and deleted automatically afterwards. The finished video is an ordinary media
asset of the studio; it is never deleted with the source.

> **Rights.** Use only movies you are authorized to use. ReelForge never downloads from streaming services, never
> bypasses DRM or any other protection and never decides whether a use is lawful (fair use, quotation, licence): the
> operator and the user remain responsible for the source and for what they publish. The pipeline favours commentary
> over long excerpts (short clips, a capped share of the movie), which is a product choice, not a legal assessment.
>
> **Google Drive is temporary storage for sources only**, never where finished videos live.

Migration `0027_movie_sources` adds the tables `movie_sources` and `movie_source_uses` and the column
`assets.movie_source_id`. The feature is off until a system administrator enables it with a working Drive.

## Lifecycle

```
importing ─→ uploading ─→ ready ⇄ processing ─→ completed
    └───────────┴─→ failed           │               │
ready / completed / failed ─→ delete_scheduled ─→ deleting ─→ deleted
```

| Status | Meaning |
| --- | --- |
| `importing` | The movie worker copies, downloads or fetches the movie, hashes it (SHA-256 and MD5) and checks it with ffprobe |
| `uploading` | A resumable upload to Google Drive (an inbox file is moved instead); Drive's size and MD5 must match |
| `ready` | Stored in Drive; can be used by a Movie Review or Recap |
| `processing` | At least one workflow run that uses it is running or waiting for review |
| `completed` | A run that used it succeeded (it produced a final render); deletion follows after the grace period |
| `delete_scheduled` / `deleting` | Retention ran out, a review succeeded (plus grace), or someone chose *Delete now* |
| `deleted` | The Drive file is in the trash (or deleted) and the local copies are gone; the row stays as history |
| `failed` | The import or the upload failed with a stable code (`failure_stage`, `failure_code`); *Retry* starts it again |

`created` exists in the schema for a source that has not been queued; the API queues imports at once.

## Adding a movie

Media → **Movie sources** → *Add movie source*, three tabs. Every import runs in the movie worker, never in a request.

1. **Server file**: a file in the studio's own import folder, `<movie_sources.local_import_root>/<workspace id>/`
   (the root defaults to `/srv/data/import/reelforge`), browsed folder by folder. A studio never sees or picks a file
   placed for another studio, nor one in the root itself; the Add dialog names the folder a studio needs when it does
   not exist yet. Paths are relative to that folder; `..`, absolute paths, Windows drive letters, NUL bytes
   and symbolic links (anywhere on the path, even ones pointing inside the root) are refused; the real path must
   still be under the root; only regular files with an MP4, M4V, MOV, MKV or WebM name. The path is checked again by
   the worker when it copies the file.
2. **Direct URL**: one public `https://` address on port 443 of a movie file the user may use. The SSRF rules of
   `app/sources.py` apply: every address the host resolves to must be public (no loopback, private, link-local,
   carrier-grade NAT, multicast, reserved or metadata addresses); the request goes to the checked address, the name
   is used only for `Host` and TLS; redirects are followed by hand (at most 3) and checked again; a page that is not a
   video (HTML, JSON…) is refused; `Content-Length` and the streamed size are capped by `max_source_bytes`; the
   download has a deadline. No cookie, credential or header of ours is sent. Only the display form (scheme, host,
   path) is stored in clear; the full address (it may be signed) is encrypted with the master key and erased once the
   movie is ready.
3. **Google Drive**: a file the operator placed in the studio's inbox, `<root>/inbox/<workspace id>/`. Only files
   whose parent is that inbox are accepted; the import moves the file into the studio's source folder (when Drive
   refuses the move, the checked copy is uploaded and the inbox file removed).

Every movie is recognized by its first bytes (an `ftyp`/QuickTime box or a Matroska header; ZIP, RAR, 7z, gzip, tar,
PDF, HTML and executables are refused before ffprobe reads them), then ffprobe must find a video stream, a duration
and a container matching the bytes. Limits: `max_source_bytes` (default 20 GiB) and `max_duration_seconds` (default
4 hours).

## Google Drive

Everything ReelForge stores lives under one root folder, found by IDs and fixed names only:

```
<root>/movie-sources/<workspace id>/<movie source id>/source.<ext>
<root>/inbox/<workspace id>/        files the operator places there for that studio to import
```

Admin → System settings → **Movie sources** → Google Drive:

* **OAuth** (`auth_mode` `oauth`, default): an OAuth client ID and secret and a **refresh token** of the Google
  account whose Drive holds the sources, with the `https://www.googleapis.com/auth/drive` scope (the inbox holds files
  the operator uploaded, which the narrower `drive.file` scope could not read).
* **Service account** (`service_account`): the JSON key of a service account. The root folder must be in a **shared
  drive** the service account is a member of (service accounts have no storage of their own).
* **Root folder ID**: the ID in the folder's URL. **Delete mode**: `trash` (default; Drive empties its trash after 30
  days) or `delete` (permanent). **Warning size**: an alert when sources use more than this (default 500 GiB).

The client secret, refresh token and service-account key are encrypted with the master key (`system-config:<key>`),
write-only, never returned by the API, never logged; access tokens stay in the worker's memory. A resumable upload's
session URL is a capability: it is stored encrypted until the upload ends.

**Test connection** (and `python -m app.google_drive_check` on the server) checks the configuration, gets an access
token, reads the account, checks that the root folder is a writable folder, uploads a tiny text file, reads it back
and deletes it permanently: nothing is left behind (if the deletion fails it is trashed and the result says so).
`--config` checks only that the settings are complete, without calling Google.

`REELFORGE_GOOGLE_API_BASE` (development and tests only, loopback addresses only) points the client at a local test
server (`tests/fake_drive.py`).

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `movie_sources.enabled` | off | Members can add sources (Drive must be configured too) |
| `movie_sources.retention_days` | 7 (1–90) | A new source expires after this many days |
| `movie_sources.max_retention_days` | 30 (1–365) | *Extend* never goes past now + this |
| `movie_sources.delete_after_success` | on | Delete after a successful review (plus the grace period) |
| `movie_sources.success_grace_hours` | 24 (0–720) | Wait after the success before deleting |
| `movie_sources.max_source_bytes` | 20 GiB | Largest movie |
| `movie_sources.max_duration_seconds` | 14 400 (60–43 200) | Longest movie |
| `movie_sources.local_import_root` | `/srv/data/import/reelforge` | Server files come from `<this>/<workspace id>/` (absolute path; one folder per studio) |
| `movie_sources.scratch_root` | empty: `<media root>/.movie-scratch` | Local working space |
| `movie_sources.delete_local_temp` | on | Delete the checked local copy after the Drive upload (off: keep it as the scratch copy) |
| `movie_sources.delete_scratch` | on | Delete a source's scratch copy once no running run uses it (after 30 minutes unused) |
| `movie_sources.frame_interval_seconds` | 10 (2–120) | Frame sampling interval (widened for long movies) |
| `movie_sources.max_frames` | 300 (10–500) | Frames per movie |
| `movie_sources.drive.*` | | See above |
| `credits.vision_per_batch` | 1 | Credits per batch of 10 frames described by the vision model |

Each source records the retention rules in force when it was added (`delete_after_success`, `delete_grace_hours`).

## Retention and deletion

* **Expiry**: `expires_at` = added + `retention_days`. *Extend* adds 1, 3 or 7 days, never past now +
  `max_retention_days` (`retention_limit` when nothing is left to add).
* **After success**: when a run that used the source **completes with a final render**, the source records
  `success_at`; with `delete_after_success` it becomes due `delete_grace_hours` later.
* **Active use**: a run that refers to a source (`movie_source_uses`, written by the Movie Source step in the run's
  first transaction) protects it while the run is `running` or `awaiting_review`. This is durable state in the
  database, not a lock in memory: a restart changes nothing. A source in use is never scheduled and *Delete now*
  answers `409 source_in_use`; the Movie Source step refuses a source that is not usable, so no new run can start on a
  source being deleted.
* **Scheduling**: the scheduler worker calls `movie_sources.maybe_schedule()` on its passes (at most every 5 minutes):
  every due source not in use becomes `delete_scheduled` (audited as `movie_source.delete_requested`, reason `expired`
  or `after_success`). It is idempotent.
* **Deletion**: the movie worker trashes (or deletes) the Drive file and its folder, removes the local import and
  scratch copies and marks the source `deleted` (`movie_source.deleted`; a file already gone counts as deleted).
  A Drive failure is retried with a growing delay (1, 2, 4… minutes, at most every 6 hours), without limit; after an
  hour of failures the admins get the `movie_sources:deletion` alert (Admin → Overview shows it too).
* **What is never deleted**: the row and its metadata (name, size, duration, checksums, dates, failure), the final
  render and every other asset, transcripts, scripts and run history. Intermediate assets made from the movie (the
  `movie_audio` track, `extracted_clip` excerpts) follow the media retention of intermediates
  ([STORAGE.md](STORAGE.md)).

## Local storage

| Path | What | Removed |
| --- | --- | --- |
| `<scratch>/imports/<source>/` | The copy being imported (`source.part`, then `source.<ext>`) | After the upload (or kept as the scratch copy when `delete_local_temp` is off); on a failed import; when the source is deleted |
| `<scratch>/movie-jobs/<source>/source.<ext>` + `source.ok` | The scratch copy every step of every run reads, downloaded from Drive **once** per source and checked against its MD5 (a lock file prevents two downloads; a partial download resumes) | When no running run uses the source, after 30 minutes unused (`delete_scratch`), and when the source is deleted |
| `<scratch>/movie-jobs/<source>/<run>/` | One run's frames (JPEG, at most 512 px wide) and working audio | When the run is no longer running or waiting for review |

The movie worker sweeps every 10 minutes. Plan disk space for the largest movie twice (import copy and scratch copy)
plus frames. Scratch files are never served by the API and never appear in a response.

## The movie worker

`python -m app.movie_worker` (systemd `reelforge-worker@movie`, enabled only where movie sources are used;
`deploy.sh` restarts it when enabled). Two lanes run side by side so a long import never holds up a review:

* **Sources**: import, upload, deletion, each under a lease on the `movie_sources` row (15 minutes, renewed while bytes
  move). A crashed worker's lease expires and another takes over; uploads resume from the stored session.
* **Step jobs** (`movie:` logical keys in `workflow_jobs`, the queue *movie* in Admin → Operations): `movie.prepare`,
  `vision.analyze` (one paid job per batch of frames), `movie.clip_extract`.

`--once` does one piece of work per lane; `--check` reports FFmpeg, ffprobe, whether movie sources are enabled,
Drive's configuration and the import folder. Its heartbeat (`movie_worker`) is expected only while movie sources are
enabled: with the feature off it is neither reported missing nor alerted on.

## The Movie Review workflow

*Use for Movie Review* / *Use for Movie Recap* on a ready source (or the templates **Movie Review from a movie
source** / **Movie Recap from a movie source**) create the source's workflow, or reuse it, and run it:

```
Movie Source → Prepare Movie ─audio─→ Transcript ─────────────┐
                    └──frames──→ Visual Analysis ─────────────┴→ Movie Timeline → Story Analysis
                                                                    └──────────────┬→ Review Script
Review Script ─┬→ Voice ─┬→ Clip Selector → Extract Source Clips → Render → Review → Publish
               └→ Subtitle ←┘ (audio)                       Review Script → Metadata → Publish
```

| Step | Cost | What it does |
| --- | --- | --- |
| Movie Source | free | Records that the run uses the source (`movie_source_uses`); the source becomes `processing` |
| Prepare Movie | free | Scratch copy (downloaded once), the sound as mono 16 kHz MP3 (a `movie_audio` intermediate asset for Transcript), scene cuts found on keyframes, frames every N seconds plus just after scene cuts (a quarter of the budget), at most `max_frames`. A movie without sound fails with `no_audio` |
| Transcript | transcription credits | The existing step on the extracted audio (pieces of 20 minutes; `TRANSCRIPTION_MAX_SECONDS`, default 3 hours) |
| Visual Analysis | `credits.vision_per_batch` per batch of 10 frames | A vision-capable Text model (Gemini, OpenAI or Anthropic) describes each frame (description, people by appearance, place, action, importance). The prompt forbids identifying anyone from their face: a name is used only when the frame shows it. Each batch is reserved, charged once, refunded when the provider refused it, held for reconciliation when the outcome is unknown; batches the provider refused leave the step completed with the others |
| Movie Timeline | free | Dialogue and visual notes in windows (10 s, wider for long movies), fitted into the prompt budget by widening the windows (a two-hour movie stays a few hundred windows) |
| Story Analysis | text credits | The existing step on the timeline; also returns setup, turning points, conflict, climax, ending and visual moments |
| Review Script | text credits | Mode `recap`, `review` or `ending_explained`; spoilers `none`, `light` or `full`; tone (neutral, cinematic, storytelling, documentary, funny, critical); target length (never more than a quarter of the movie); sections with `source_ranges` (the moments they talk about), facts and opinions kept apart |
| Clip Selector | free | Excerpts of 2–8 seconds per section, from its own ranges first, then timeline windows sharing its words, then the matching position; never overlapping (no moment twice), at most `max_clips` (30), the total kept under a quarter of the movie when the narration allows it (reported otherwise), never the whole movie |
| Extract Source Clips | free | Cut from the scratch copy (stream copy for an MP4 source, else H.264/AAC), stored as `extracted_clip` assets recording `movie_source_id`, source start and end |
| Voice, Subtitle, Render, Review, Publish | as before | Consecutive clips of one section share its narration as one window |

Steps are durable jobs: a worker restart resumes them; a step that failed is retried with the run's *Retry* (a new run;
the scratch copy is reused). The run page and the source's detail show each step with its counts (frames sampled,
batches analysed, frames described, clips cut).

## Permissions

| Action | Owner | Admin | Editor | Viewer |
| --- | --- | --- | --- | --- |
| See movie sources and their details | yes | yes | yes | yes |
| Add, extend, retry an import | yes | yes | yes | – |
| Use for Movie Review / Recap (start a run) | yes | yes | yes | – |
| Delete now | yes | yes | – | – |
| Configure (Admin → System settings → Movie sources), Drive test, every studio's sources | system administrators |

Sources belong to one studio: another studio gets `404` for every one of them.

## API

| Method and path | Who | |
| --- | --- | --- |
| `GET /api/movie-sources?q=&status=&source_type=&limit=&offset=` | members | One page and the server's movie-source settings |
| `GET /api/movie-sources/config` | members | Which source types work, retention rules |
| `GET /api/movie-sources/local-files?folder=` | editors | One folder of the studio's import folder (relative names and sizes) |
| `GET /api/movie-sources/drive-files?page_token=` | editors | The studio's Drive inbox |
| `POST /api/movie-sources` | editors | `{source_type: local\|url\|drive, path\|url\|drive_file_id, name?, project_id?}` → `importing` |
| `GET /api/movie-sources/{id}` | members | Details and the runs that used it |
| `POST /api/movie-sources/{id}/import` · `/retry` `{stage: import\|upload}` | editors | Start a failed import or upload again (or a failing deletion now) |
| `POST /api/movie-sources/{id}/extend` `{days: 1\|3\|7}` | editors | |
| `DELETE /api/movie-sources/{id}` | owners, admins | `delete_scheduled`; `409 source_in_use` while a run uses it |
| `POST /api/movie-sources/{id}/movie-review` · `/movie-recap` | editors | `{mode?, spoiler_level?, tone?, duration?, language?, section_count?, platform?, project_id?}` → the workflow and the run |
| `GET /api/admin/movie-sources` | system admins | Every studio's sources with Drive IDs and retry state, and the Drive summary |
| `POST /api/admin/system-config/movie_sources/drive/test` | system admins | The connection test |

Errors carry a stable `code` (`movie_sources_disabled`, `drive_not_configured`, `invalid_path`, `symlink_refused`,
`blocked_url`, `too_large`, `unsupported_type`, `invalid_drive_file`, `source_in_use`, `source_not_ready`,
`source_expired`, `retention_limit`…). Responses never include a scratch path, a full URL, a Drive credential, an
access token, an upload session or (outside the admin console) a Drive ID.

## Notifications, audit and alerts

* Notifications to the member who added the source: `movie_source.ready`, `movie_source.failed` (link to the source).
  Review results use the existing run notifications. Deletion failures go to system administrators only (alert).
* Audit actions: `movie_source.created`, `.import_started`, `.ready`, `.retention_extended`, `.delete_requested`,
  `.deleted`, `.import_failed`, `.drive_failed`. Details hold codes, sizes and reasons, never a URL or a credential.
* System alerts (12-hour cool-down): `movie_sources:deletion` (deletions failing for over an hour),
  `movie_sources:drive_usage` (above the warning size), `movie_sources:drive` (enabled without a configured Drive).
  Admin → Overview lists them under *Needs attention*, with the imports that failed in the last 24 hours (a warning
  when Google Drive refused an upload, otherwise for information: a member's link or file that is not a movie);
  each opens Admin → System settings → Movie sources.
* Readiness (Admin → Verification): section *Movie sources*: Google Drive configured, import folder present, scratch
  space writable, Drive space, deletions. Drive itself is not called by readiness.

## Credits and storage accounting

Visual Analysis is the only new paid operation (`credits.vision_per_batch`, reference
`vision-reserve:<step>:batch:<n>`, `vision:<step>:batch:<n>`, `vision-refund:<step>:batch:<n>`); an uncertain batch
appears in Admin → Reconciliation like any paid job. The Drive copies are the operator's storage and do not count in
any studio's quota. Admin → Operations (*Movie source Drive*) and Admin → System settings → Movie sources show the
files, bytes, oldest source, sources expiring within 24 hours and failing deletions, counted from the table: Drive's
own quota is never read or claimed.
The extracted audio and clips are studio media (they count, and expire as intermediates); the final render is
ordinary media.

## Backups

The database dump keeps every source's metadata. The Drive copies, the import folder and the scratch space are **not**
backed up: they are temporary by design; the media manifest lists final renders and uploads only
([BACKUP_RECOVERY.md](BACKUP_RECOVERY.md)). After a restore, a source whose Drive file is gone shows its error on the
next use or deletion; deleting it is harmless.

## Security review

| Risk | Control |
| --- | --- |
| Path traversal and links in the import folder | Relative parts only, `lstat` of every component, real path containment, regular files, re-checked by the worker |
| SSRF through direct URLs | `https` on 443, public resolved addresses only, pinned address, redirects re-checked, type and size limits, deadline |
| Credential leaks | Drive secrets encrypted and write-only; tokens in memory; session URLs and signed URLs encrypted; no secret in logs, audit, notifications or responses (tests assert it) |
| Another studio's data | Every query is scoped to the active workspace; inbox imports verify the parent folder |
| Uploading somewhere else | A resumable session URL must stay on the Drive API host |
| Deleting a movie in use | Durable use records and run status, checked under the source's row lock |
| Malicious files | Magic-byte checks before ffprobe; ffprobe and FFmpeg run without a shell, with time limits |
| Identity guessing | The vision prompt forbids it; names come only from dialogue or on-screen text |
| Cost | Frames capped (`max_frames`), batches priced and reserved before the run continues, refunds on refusal |

## Limits

* One review downloads the movie once per source and server; several movie workers on one server share the scratch
  copy (a lock file); workers on different servers would each download it once.
* Transcription stops at `TRANSCRIPTION_MAX_SECONDS` (3 hours by default) even when movies may be longer.
* A movie without a sound track cannot be reviewed automatically (`no_audio`).
* Visual analysis needs a model that accepts images; a text-only model fails the batches with `invalid_request`
  (refunded).

Manual verification: [MOVIE_SOURCE_VERIFICATION.md](MOVIE_SOURCE_VERIFICATION.md).
