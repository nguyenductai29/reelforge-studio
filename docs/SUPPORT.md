# Customer support (Phase 18C)

Users contact ReelForge from inside the app; system admins answer from Admin → **Hỗ trợ**. The two sides use the same thread, and each new message reaches the other side as a notification (see [NOTIFICATIONS.md](NOTIFICATIONS.md)).

Code: `app/support.py` (rules), the endpoints in `app/main.py`, `frontend/src/app/support/*`, `frontend/src/components/reelforge/support.tsx` and `admin/admin-support.tsx`. Migration `0017` adds `support_tickets` and `support_messages`.

## User side

- **Opening it.** Account menu → **Hỗ trợ** opens `/support`: the user's requests, newest activity first, and **Yêu cầu mới**.
- **A request** has a subject, a category and a description. The categories are:
  - Billing / payment;
  - Credits;
  - AI generation;
  - Publishing;
  - Account;
  - Storage;
  - Bug / technical issue;
  - Other.
- **The ticket page** (`/support/<id>`) shows:
  - the subject, status, category and date;
  - the thread, with replies signed "ReelForge support", never the admin's own account;
  - a reply box;
  - **Đóng yêu cầu**.
- **Replies.** A user's reply reopens a resolved ticket for support. A closed ticket takes no reply; the user opens a new one.
- **Context from another page.** A page can link to `/support?new=1&run_id=…` (also `project_id`, `payment_order_id`, `publication_id`) to attach the record it is about. Only these IDs are sent, and each must belong to the user's studio, or the request is refused with `invalid_context`. Nothing is copied from the record: no provider response, log or job payload. The admin opens it through the existing admin views.

## Who sees what

| Viewer | Sees |
| --- | --- |
| The ticket's creator | The ticket |
| The studio's owner | Every ticket of the studio |
| Another studio | Nothing: 404, as if the ticket did not exist |
| System admins | Every ticket, in Admin → Hỗ trợ |

Messages are append-only: nobody can edit or delete them. There are no internal admin notes, so nothing hidden can reach a user.

## Statuses

| Status | Meaning | Set by |
| --- | --- | --- |
| `waiting_support` | Support has to answer | A new ticket, and every user reply |
| `waiting_user` | Waiting on the user | An admin reply, unless the ticket is resolved |
| `open` | Open, no one is waiting | An admin, by hand |
| `resolved` | Solved; a user reply reopens it | An admin |
| `closed` | Final: no more replies | The user or an admin (`closed_at` is recorded) |

Priority is `normal` or `high`, and only admins set it.

## Admin console

Admin → **Hỗ trợ** follows the fixed-height table pattern: sticky header, an internally scrolling body, pagination.

- **Search:** ticket ID prefix, subject, user email or studio name, case-insensitive.
- **Filters:** status, category, priority.
- **Columns:** request, user, studio, category, status, updated, priority, action.
- **Opening a ticket** shows the thread, a status select, a priority select and a reply box. "Mark resolved when sending" replies and resolves in one step.

Admins hear about new tickets and user replies through the bell. The notification links straight to `/admin?tab=support&ticket=<id>`. The header shows the number of requests awaiting an answer.

## API

| Endpoint | Access |
| --- | --- |
| `GET /api/support/tickets?limit&offset` | The user's tickets (`{items, total, limit, offset}`) |
| `POST /api/support/tickets` | `{subject, category, description, run_id?, project_id?, payment_order_id?, publication_id?}` → 201 with the thread |
| `GET /api/support/tickets/{id}` | One ticket with its thread |
| `POST /api/support/tickets/{id}/messages` | `{body}`; 409 `ticket_closed` |
| `POST /api/support/tickets/{id}/close` | Closes it |
| `GET /api/admin/support?q&status&category&priority&limit&offset` | System admins |
| `GET /api/admin/support/{id}` | System admins (author emails included) |
| `POST /api/admin/support/{id}/messages` | `{body, status?}` |
| `PATCH /api/admin/support/{id}` | `{status?, priority?}` |

Subjects are limited to 200 characters and messages to 5,000. The form reminds users never to send passwords, API keys or card details.

## Limits

- No attachments. Users describe the problem, and a context ID points at the record.
- No email delivery: replies are in-app notifications only.
- One support queue for all system admins; tickets are not assigned to a particular admin.
