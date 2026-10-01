# Load baseline (v1.0)

A basic load check of the paths every user or worker hits often: sign-in, the dashboard, notifications, job claiming
and payment callbacks. It measures, it does not tune: nothing was optimized for these numbers. Re-run it after a
change that touches one of these paths, and once on the production server's hardware before launch.

## How to run

```bash
python tests/load_check.py --database-url postgresql://postgres:postgres@127.0.0.1:5432/reelforge_load_test
```

* The database must be an isolated PostgreSQL database whose name contains `test`; the script drops and creates it.
  **Never point it at the production database.**
* It copies `app/` and `migrations/` to a temporary folder with their own `instance/bootstrap.json` and master key,
  migrates, starts one uvicorn process on 127.0.0.1 (as `reelforge-api.service` does) and seeds it through the API.
* No external service is contacted: email is off; payOS callbacks are signed locally with a throwaway checksum key,
  the way payOS signs them, and verified by the payOS SDK in the API.
* Each simulated client sends its own `CF-Connecting-IP`, as Cloudflare does; the per-address and per-account
  limits stay in force (each account signs in four times in total, under the limit of 10 per 15 minutes).
* Options: `--users`, `--logins-per-user`, `--requests`, `--concurrency`, `--concurrency-login`, `--jobs`,
  `--workers`, `--callback-repeats`, `--json results.json`, `--keep` (keeps the copy and its `api.log`).

## Measured baseline — 2026-10-02

Machine: development workstation, AMD Ryzen 7 9700X (8 cores / 16 threads), 31 GB RAM, Windows 11, Python 3.14.7,
PostgreSQL 16.2 on the same machine. One API process, default SQLAlchemy pool (5 + 10 overflow), default
FastAPI/AnyIO thread pool. Data: 51 studios (50 users and the administrator), 3 projects each, 50 notifications per
user, 2,000 queued jobs, 51 pending payOS orders.

| Scenario | Requests | Concurrency | Per second | p50 ms | p95 ms | p99 ms | Max ms | Errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| register | 50 | 10 | 60.0 | 158.7 | 209.0 | 227.2 | 227.2 | 0 |
| login | 100 | 10 | 94.7 | 98.1 | 137.8 | 141.8 | 147.1 | 0 |
| dashboard | 1000 | 20 | 166.1 | 118.8 | 142.5 | 156.3 | 180.9 | 0 |
| notifications unread-count | 1000 | 20 | 453.8 | 39.0 | 77.0 | 90.4 | 106.8 | 0 |
| notifications list | 500 | 20 | 298.3 | 61.5 | 106.3 | 118.7 | 125.6 | 0 |
| job claim (SKIP LOCKED) | 2016 | 8 | 1142.6 | 6.8 | 8.9 | 9.8 | 49.9 | 0 |
| payment callback (payOS) | 153 | 10 | 80.5 | 120.2 | 145.3 | 162.0 | 189.5 | 0 |

Correctness under load (checked by the script, a failure stops it):

* job claim: 2,000 claims by 8 concurrent claimers, 2,000 distinct jobs, none claimed twice (the 16 extra polls found
  the queue empty, as each worker's last poll does);
* payment callback: every callback delivered three times; 51 of 51 orders paid, 51 credit grants (no double credit).

A second run on the same machine was within a few percent of these numbers.

## Reading the numbers

* **Sign-in and registration** cost what the password hash costs (scrypt, n = 2^14, r = 8): about 100 ms of CPU per
  attempt, by design. One process signs in roughly 95 people a second, far beyond what a studio needs.
* **Dashboard** is the heaviest read (projects, workflows, assets, limits, account in one call): about 120 ms at
  20 concurrent requests and 166 requests a second from one process.
* **Notifications**: the unread count every open tab polls answers in about 40 ms at 20 concurrent requests.
* **Job claiming** is not a bottleneck: over a thousand claims a second, with no double claim.
* **Payment callbacks**: about 80 a second, duplicates included, each settling exactly once.

Revisit only if production monitoring (`/internal/metrics`, the request duration histogram) shows a real problem;
for example a dashboard p95 above 500 ms at the studio's real concurrency. Possible levers then, in order: more API
processes behind the same port (the rate limits and job claims are already shared through PostgreSQL), a larger
database pool, and only then query changes.
