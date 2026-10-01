# Operations

Phase 13 adds the small tools needed to run ReelForge reliably on one server.

## Default models per task

**Settings → AI → Default model per task** (`GET`/`PUT /api/settings/default-models`) chooses one model each for text, image, video, voice and transcription. A step resolves its model as follows:

1. the model chosen in the step's own settings, always;
2. else the workspace default for its task, while it is enabled and supported by that step;
3. else the first enabled compatible model, in the order the workspace added them.

A default must be an enabled model of the right task in the same workspace (`422 invalid_default_model` otherwise). Disabling it later makes steps fall back to rule 3; nothing breaks. Readiness and the Run dialog show the model each step will use.

## Worker health

Every worker reports a heartbeat at most every 15 seconds into `worker_heartbeats` (migration 0014). One row per worker kind; the latest process of that kind wins. **Admin → Operations** (`GET /api/admin/workers`, system admins only) shows each worker as:

- `ok`: seen within 120 seconds;
- `stale`: not seen recently (stopped, crashed or stuck);
- `error`: seen, but its last pass failed (the scheduler reports this);
- `missing`: never started on this database.

The workers:

| Worker | Queue |
| --- | --- |
| `text_worker` | `text:` |
| `image_worker` | `image:` |
| `video_worker` | `video:` |
| `voice_worker` | `voice:` |
| `render_worker` | `render:` (renders and clip extraction) |
| `source_worker` | `source:` (web pages and transcription) |
| `youtube_worker` | `publish:youtube:` |
| `social_worker` | `publish:tiktok:`, `publish:facebook:` |
| `scheduler_worker` | none; it queues scheduled publications |

## Jobs and stuck work

`GET /api/admin/jobs` (system admins; filters `state`, `queue`, `limit`, `offset`; the response has `total`) lists recent jobs of every workspace, one page at a time. It returns safe fields only:

- queue and channel;
- state and attempt count;
- worker;
- workspace, run and step IDs;
- timestamps;
- the last error, with any URL removed.

Payloads are never returned: they can hold prompts or URLs. The response also has counts per queue and state, and a **stuck-work audit** (`jobs.stuck_jobs`):

- **expired leases**: a worker claimed the job and stopped reporting. The next worker for that queue reclaims it automatically and logs `job_lease_reclaimed` with the previous worker, which is the recovery audit trail. Paid jobs follow their own rules on reclaim: a provider call that may have happened is held for credit reconciliation, never sent again.
- **overdue**: queued for more than 10 minutes after it was due. Usually the worker for that queue is not running; check worker health.
- **orphan steps**: a step still queued or running although none of its jobs is. Inspect the run, and retry or reconcile it.

## Storage

- **Settings → Storage** (`GET /api/storage`) shows the workspace's stored bytes against `WORKSPACE_MEDIA_QUOTA_BYTES`, by kind (video, audio, image, document). The dashboard also returns `storage`.
- `GET /api/admin/storage` lists every workspace's usage, largest first.
- `python -m app.media_maintenance --usage` prints the same from the server.

## Media cleanup

```bash
python -m app.media_maintenance                  # dry run: lists what would be removed
python -m app.media_maintenance --apply          # removes it
python -m app.media_maintenance --orphans        # also lists files with no asset row (reads the asset table)
python -m app.media_maintenance --orphans --apply
```

Only files at least 24 hours old are touched (`--older-than-hours`, minimum 24). Links and junctions are never followed. Three kinds of leftovers are handled:

- **`.part` downloads** left by a crashed media worker: exact `<uuid>.part` names directly in a workspace folder.
- **Worker temp folders**:
  - `<media>/.render-tmp/<job>` (render and clip extraction);
  - `<media>/.source-tmp/<job>` (transcription audio);
  - `<media>/.publish-tmp/<job>.mp4` (the upload copy).

  Workers remove these themselves; leftovers mean a worker was killed. A folder that contains any link is skipped whole.
- **Orphans**: UUID-named files in a workspace folder with no `assets` row, for example when the database was restored from an older backup. The row is checked again just before deleting.

Run a dry run first, and keep a backup of the media folder.

## Admin console (Phase 15)

The Admin page fits the window and never scrolls itself. Each tab is one table with a sticky header, a body that scrolls on its own, and pagination at the bottom. Every collection is paginated and searched on the server; the browser never downloads the full user or studio list.

| Endpoint (system admins) | Filters | Notes |
| --- | --- | --- |
| `GET /api/admin` | — | Summary only: `counts` (users, active users, admins, studios, plans, pending reconciliation, failed jobs in 24 h, pending payments, each a `COUNT` query; and stuck jobs from the stuck-work audit), the plans, and payment-provider readiness |
| `GET /api/admin/users`, `/users/{id}` | `q` (email, case-insensitive), `role` (`admin`/`member`), `status` (`active`/`locked`) | Detail adds open sessions and studios |
| `GET /api/admin/workspaces`, `/workspaces/{id}` | `q` (studio name or owner email), `plan`, `status` (`active`/`expired`/`paused`/`canceled`) | Detail adds members, counts, storage, recent ledger entries and orders |
| `GET /api/admin/payments` | `q` (email, studio, provider reference, or order code), `provider`, `status` | No checkout URL or provider payload |
| `POST /api/admin/payments/{id}/refresh` | — | Asks the order's provider server to server; there is no "mark paid" action |
| `GET /api/admin/payment-providers` | — | `{provider, method, configured}` only |

List responses are `{items, total, limit, offset}` (`limit` 20 by default, at most 100). `q` is matched with `LIKE` after escaping `%`, `_` and `\`, so it is always a literal substring. Account creation (`POST /api/admin/accounts`), locking (`PUT /api/admin/users/{id}`; an admin cannot lock themselves or the last active admin), plan and status changes and credit adjustments (an append-only ledger entry with a reason) are unchanged. Migration 0015 adds indexes on `workspaces.owner_id` and on `payment_orders (created_at)` and `(provider, status)` for these pages.

## Never logged or returned

Provider keys, payment-provider credentials and hash keys, OAuth access and refresh tokens, signed media URLs, cookies and upload session URLs. Logs carry IDs, codes and sizes; job payloads are summarized to safe fields (`app/logs.py`).
