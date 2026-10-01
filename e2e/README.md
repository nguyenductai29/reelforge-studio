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

Playwright starts three servers on loopback: the SMTP sink (`tests/smtp_sink.py`, port 2526, every message saved in
`e2e/.mail/`), the API (port 8010, `e2e/.stack/api`) and the built frontend (port 3010). The specs run in order in
one worker, on one database; run `prepare.py` before each full run.

Each simulated person sends their own `CF-Connecting-IP` (TEST-NET-2 addresses). The API believes that header only
from its trusted local proxy, exactly as in production behind Cloudflare, and its sign-in limits are per address,
so the suite exercises the real client-address path instead of relaxing the limits.
