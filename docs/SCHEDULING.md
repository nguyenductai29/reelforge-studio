# Scheduled Publishing

A publication can be queued now or at a chosen time. Scheduling only delays an upload the owner has already asked for. Nothing is ever published because a workflow finished.

## States

```text
scheduled ──(scheduler, at scheduled_for)──→ queued ──→ uploading ──→ succeeded
    │                                          │             └──────→ failed / needs_attention
    └──→ cancelled                             └──→ cancelled (before a worker claims it)
```

| State | Meaning |
| --- | --- |
| `scheduled` | waiting for `scheduled_for`; no upload job exists yet |
| `queued` | an upload job is waiting for its channel's worker |
| `uploading` | a worker started sending media |
| `succeeded` | the platform accepted it. `published_at` is set when the video actually went live; it stays empty for a TikTok inbox draft or a Facebook draft Reel |
| `failed`, `needs_attention` | see `docs/MULTI_PLATFORM_PUBLISHING.md` |
| `cancelled` | the owner cancelled it before the upload started |

All times are stored and returned in UTC (`scheduled_for`, `published_at`). The publish dialog takes a local date and time and sends it as UTC. A time already in the past means "now". A time more than one year ahead is refused (`invalid_schedule`).

## The scheduler worker

```bash
python -m app.scheduler_worker          # a pass every 15 seconds
python -m app.scheduler_worker --once   # one pass
```

Each pass calls `publications.dispatch_due`, which:

1. locks the due `scheduled` publications (`FOR UPDATE SKIP LOCKED` on PostgreSQL, so several schedulers never take the same row);
2. checks each one again under the lock;
3. in the same transaction, verifies the run is still approved and the channel still connected, then queues the upload job and sets the publication to `queued`.

This makes the scheduler safe to restart and idempotent:

- a crash before the commit leaves the publication `scheduled` for the next pass;
- a crash after it leaves a queued job, which the channel's worker picks up;
- a second pass finds nothing to do.

A publication whose channel was disconnected becomes `failed` with `schedule:connection_required`. One whose run is no longer approved becomes `failed` with `schedule:not_approved`. Both can be retried after fixing the cause.

## Rescheduling and cancelling

Allowed while the publication is `scheduled`, or `queued` with a job no worker has claimed yet:

- `PUT /api/publications/{id}/schedule` with `{"scheduled_for": "<UTC ISO time>"}` moves it; `null` means as soon as possible. A queued job is withdrawn and a new one is queued at the new time.
- `POST /api/publications/{id}/cancel` cancels it and withdraws its job.

Once a worker has claimed the job, both return `409 upload_started`. A cancelled publication can be published again with the publish dialog: the same row is reused.

## Calendar

The **Calendar** page lists every channel's publications by scheduled time, else publishing time, else creation time. It offers month, week and list views, and the list offers **Reschedule** and **Cancel**. `GET /api/publications?start=…&end=…` (UTC) selects a range.
