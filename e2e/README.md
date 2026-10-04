# End-to-end tests (Playwright)

Browser tests of the critical flows, on a disposable stack. They never touch `instance/`, the development database
or any real provider.

| Spec | Flow |
| --- | --- |
| `01-auth` | first-run setup, sign-out and sign-in, wrong password; forgot password → emailed link → new password (other sessions end, the link works once); sessions list and "sign out all other sessions"; 2FA enrolment (a wrong code is refused), sign-in with a code, a recovery code that works once |
| `02-team` | email verification before inviting; an emailed invitation accepted by a new account and by an existing one; own studio from the switcher; switching; a viewer cannot create (the API refuses it too); removing a member takes effect at once; no project leaks between studios |
| `03-studio` | onboarding checklist, project creation, workflow from a template |
| `04-billing` | manual VietQR: the buyer reports the transfer, an administrator confirms it, the plan activates once, the receipt email and notification arrive |
| `05-support` | a support request, the administrator's reply as a live notification and an email, mark all as read |
| `06-admin` | every admin tab opens without an error; the audit log filters on the server; a non-admin gets 403; a request from a foreign origin is refused |
| `07-layout` | Settings and Admin never scroll the page (1366×768 to 1920×1080), nor on a phone or a tablet; a window too short for the page scrolls the region under the top bar, and dialogs scroll on their own |
| `08-members` | Settings → Members: search, filters and pages; invitations and member actions; nothing beyond the member's role |
| `09-home` | Home: the overview, what needs attention, quick create and recent work; a viewer only reads |
| `10-admin-overview` | Admin → Overview is the first tab; its figures and what needs attention each open the tab that handles it |
| `11-wide-layout` | Sixteen pages (Media → Movie sources included) at 390×844, 768×1024, 1024×768, 1366×768, 1440×900, 1680×1050 and 1920×1080: no sideways scrolling, the content uses the width beside the sidebar, workflow diagrams not clipped, the plans side by side; at 1680 and 1920 the admin figures in one row, the panels side by side, the members table across the page and more templates a row |
| `12-movie-sources` | Movie sources: the Drive connection test leaves nothing behind; a movie from the studio's own import folder on the server (`<import root>/<workspace id>/`), a direct URL (and one redirecting to an internal address, refused) and a Drive inbox file become Ready, each with its project in the table; details, retention +3 days; *Use for Movie Review* runs the workflow; deleting the source is refused while the run uses it; the run's progress on the source; after approval the source is used; *Delete now* ends Deleted; Admin → Overview lists the failed import and Admin → Operations the movie source Drive |

## Run locally

```bash
python e2e/prepare.py                 # copies the API and the frontend into e2e/.stack, migrates, builds (SQLite)
cd e2e && npm ci && npx playwright install chromium
npx playwright test
```

* PostgreSQL instead of SQLite: `E2E_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/reelforge_e2e_test python e2e/prepare.py`
  (an isolated, disposable database; CI does this).
* Run again on a clean database without rebuilding the frontend: `python e2e/prepare.py --skip-build`.
* A Chromium already on disk: `E2E_CHROMIUM_PATH=/path/to/chrome npx playwright test`.

Playwright starts four servers on loopback: the SMTP sink (`tests/smtp_sink.py`, port 2526, every message saved in
`e2e/.mail/`), an in-memory Google Drive (`tests/fake_drive.py`, port 8021, for movie sources), the API (port 8010,
`e2e/.stack/api`) and the built frontend (port 3010). The specs run in order in one worker, on one database; run
`prepare.py` before each full run.

The movie source spec runs the movie workers itself through `e2e/movie_driver.py`, inside `e2e/.stack/api`, with test
doubles only: the fake Drive, an in-process mock for direct URLs behind a resolver answering a public address (the SSRF
rules stay on), fake FFmpeg and fake AI providers. `prepare.py` creates the import folder (`e2e/.stack/import`, one
MP4-shaped file) and the dummy FFmpeg paths the API is started with.

Each simulated person sends their own `CF-Connecting-IP` (TEST-NET-2 addresses). The API believes that header only
from its trusted local proxy, exactly as in production behind Cloudflare, and its sign-in limits are per address,
so the suite exercises the real client-address path instead of relaxing the limits.
