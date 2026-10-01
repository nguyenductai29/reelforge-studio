# Notifications (Phase 18B)

ReelForge tells users what happened while they were elsewhere: a bell in the header shows an unread count (up to "99+") and the latest notifications. **View all** opens `/notifications`.

Code: `app/notifications.py` (model, events, reading), the endpoints in `app/main.py`, and `frontend/src/components/reelforge/notifications.tsx` (stream, bell, rows). Migration `0017_notify_support_verify` adds the `notifications` table.

## What is notified

| Type | When | Who |
| --- | --- | --- |
| `run.completed`, `run.failed`, `run.needs_attention`, `run.awaiting_review` | A run reaches that state while it advances (a worker finished a step, or an approval moved it on). A failed Render reads "Video render failed"; a review after a render reads "Video ready for review" | Studio members |
| `publish.scheduled`, `publish.succeeded`, `publish.failed`, `publish.needs_attention` | A post is scheduled; an upload succeeds; an upload fails for good, needs attention, or a scheduled post cannot go out | Studio members |
| `payment.succeeded`, `payment.failed` | A payment order is paid (plan activated or extended), or fails | Studio owners |
| `payment.unapplied` | A paid order could not be applied to its subscription | System admins |
| `credits.low` | A debit takes the balance below `CREDITS_LOW_THRESHOLD` (default 20; 0 turns it off) | Studio owners |
| `credits.adjusted` | An admin adjusts the studio's credits (with the reason the studio already sees in its credit history) | Studio owners |
| `storage.warning`, `storage.critical`, `storage.full` | A write takes the studio past 80, 90 or 100 % of its quota | Studio members |
| `support.new`, `support.reply` | A user opens a ticket or replies | System admins |
| `support.reply`, `support.status` | Support replies, or changes the ticket's status | The ticket's creator |

### Not spammy

- **Run starts are silent.** The user is on screen when a run starts. A run notifies only when it changes state while advancing, and only for the four outcomes above.
- **Each event notifies once.** Every machine notification carries a `dedupe_key` (for example `run:<id>:completed`, `payment:<order>:paid`, `credits_low:<ledger reference>`, `storage:<studio>:warning:<day>`). It is inserted with `ON CONFLICT DO NOTHING` on `(user_id, dedupe_key)`. A webhook delivered twice, a second worker pass or a retried request therefore adds nothing and never fails the transaction that caused it.
- **Thresholds fire on crossing.** A low balance is reported when a debit crosses the threshold, not on every later debit. A storage level is reported at most once a day per level.

## Privacy

- A notification belongs to one user and is written in the same transaction as the change that caused it, so a rolled-back change notifies nobody.
- Lists, counts, the stream and "mark read" only show a user notifications from studios they still belong to, plus their personal and admin ones. Another user's notification answers 404.
- `payload` holds display parameters only: names, counts, IDs and the channel. It never holds provider data, job payloads, tokens or errors from providers.
- The server keeps an English `title`/`message`. The browser shows a localized text built from `type` and `payload`.

## API

| Endpoint | Returns |
| --- | --- |
| `GET /api/notifications?limit&offset&unread` | `{items, total, limit, offset, unread}`, newest first (limit ≤ 100) |
| `GET /api/notifications/unread-count` | `{unread}` |
| `POST /api/notifications/{id}/read` | `{id, unread}` |
| `POST /api/notifications/read-all` | `{updated, unread}` |
| `GET /api/notifications/stream` | Server-Sent Events (below) |

## Realtime: Server-Sent Events

`GET /api/notifications/stream` is authenticated by the session cookie like every other request. No token travels in the URL.

```
retry: 5000

id: 42
event: notification
data: {"id": 42, "type": "support.reply", "params": {...}, "link": "/support/…", ...}

event: unread
data: {"unread": 3}

: ping
```

**Events**

- `notification`: a new notification; its `id` is the event id.
- `unread`: the unread count, sent on connect and whenever it changes, including when another tab marks something read.
- `: ping`: a comment every 15 s of silence, so idle proxies keep the connection open.

**Resuming**

- The browser sends `Last-Event-ID` when it reconnects, and the server replays everything after it.
- A new stream starts from now; older notifications are in the list.

**Ending**

- The server ends each stream after `REELFORGE_SSE_MAX_SECONDS` (default 300). The browser reconnects after `retry` (5 s) and resumes, which keeps connections short-lived behind proxies.
- The session is checked on every pass. Signing out, or being locked, ends the stream with an `end` event, and the client stops.

**Server work.** The server checks for new rows every `REELFORGE_SSE_POLL_SECONDS` (default 3). Each check is three small indexed queries per open stream, enough for a home server with dozens of users online. Admin → Kiểm định shows the number of open streams.

**Headers.** `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`. `no-transform` stops Next.js, Cloudflare and other proxies from compressing the stream, because compression would buffer the events. `X-Accel-Buffering` turns buffering off in nginx.

**Client** (`NotificationStream`):

- one `EventSource` per tab;
- on a `notification` event, the lists refresh and a toast shows the localized title if the tab is visible;
- if the connection fails hard (for example a restarting API), it is reopened after 10, 20, 40, then 60 s;
- while it is down, the badge and the open lists poll every 30 s, so the UI keeps working without realtime.

### Proxies

**Next.js.** The frontend forwards `/api/*` to the API. The stream passes through unbuffered; it was tested through `next start` with events arriving in under a second.

**nginx.** If nginx sits in front, keep the route unbuffered and allow long reads:

```nginx
location /api/notifications/stream {
    proxy_pass http://127.0.0.1:3001;      # the Next.js frontend (or the API directly)
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;                   # X-Accel-Buffering: no also asks for this
    proxy_cache off;
    gzip off;
    proxy_read_timeout 1h;
}
```

**Cloudflare / Cloudflare Tunnel.** Server-Sent Events work through `cloudflared`.

- Cloudflare closes a response that sends nothing for about 100 s. The 15 s ping and the 5-minute stream lifetime keep well inside that.
- `no-transform` keeps Cloudflare from compressing the stream.
- After a Cloudflare or tunnel restart, browsers reconnect by themselves, and polling covers the gap.

To check a deployment, open Admin → Kiểm định → **Kiểm tra luồng thông báo**. It opens the stream from your browser and passes when the first event arrives.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `REELFORGE_SSE_POLL_SECONDS` | 3 | Seconds between checks for new notifications per stream (0.1–30) |
| `REELFORGE_SSE_MAX_SECONDS` | 300 | Seconds before the server ends a stream, so the client reconnects (1–3600) |
| `CREDITS_LOW_THRESHOLD` | 20 | Balance under which owners are told once per crossing; 0 turns it off |
