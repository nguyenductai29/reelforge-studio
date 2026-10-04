# Credit reconciliation

Phase 3.7 resolves uncertain paid workflow operations using the existing credit account, append-only ledger and usage events. It adds no pricing system and makes no provider calls from an admin reconciliation action.

## Reservation lifecycle

The balance is debited when a paid step is queued, in the same transaction as the run, step and durable job. The queued payload freezes the credit amount. A successful step records usage without debiting the balance again.

| Operation | Video and image (per job, Phases 4–5) | Legacy video | Text reference | Balance change |
| --- | --- | --- | --- | --- |
| Reserve | `<kind>-reserve:<step_id>:<operation>` | `reserve:<run_id>` | `text-reserve:<step_id>` | minus reserved credits |
| Finalize usage | `<kind>:<step_id>:<operation>` | `video:<step_id>` | `text:<step_id>` | none; append a usage event |
| Refund | `<kind>-refund:<step_id>:<operation>` | `refund:<run_id>` | `text-refund:<step_id>` | plus exactly reserved credits |

`<kind>` is `video`, `image` or `voice` (Phase 6), and `<operation>` names one paid provider call:

- `scene:<n>`: one scene's clip or image;
- `image:<n>`: the n-th image of one prompt;
- `single`: one clip or one narration.

Every video, image or voice job carries its three references in its payload. Reconciliation accepts them only when they name that job's step and operation. Video jobs queued before Phase 5 carry no references and keep the per-run references in the "Legacy video" column. Those incidents and their past decisions still resolve unchanged. Text keeps its step-based references. See `docs/IMAGE_GENERATION.md` and `docs/MULTI_SCENE_VIDEO.md`.

A step that makes several files (one per scene or image) has one job per file.

Voice narrations (`docs/VOICE_GENERATION.md`) follow the same rules as images: a definite rejection is refunded, while a lost response, a provider error after sending, or an invalid or empty answer is held and decided per narration. Rendering (`docs/RENDERING.md`) calls no paid provider: it is free by default, and with `RENDER_CREDITS_PER_JOB` set, a failed render is always refunded (`render-refund:<step>:final`), so it never needs reconciliation. Subtitles are free. Its credits are reserved together, after checking that the balance covers all of them, so the step never holds part of its cost.

`reserved → charged` and `reserved → refunded` are mutually exclusive outcomes. `needs_attention` means the reservation is still awaiting a decision, not that the provider definitely charged. The credit amount is checked against the original debit and immutable job quote; the operator cannot enter a different amount.

## Automatic outcomes

Video is refunded when a failure is known to precede accepted work: a missing key or invalid local configuration before submit, an unsupported provider, expiry while still queued, a clearly full workspace, or a definitive submission rejection (`invalid_request`, `authentication_error`, `billing_error`, `not_found`, `rate_limited`). HTTP 413 maps to invalid request. These retain the existing adapter mappings.

Immediately before the first video submission, the worker compares recorded workspace asset bytes to `workspace_media_quota()`. If used bytes are at least the limit, it fails the step and refunds without submitting. This does not reserve storage, predict video size or guarantee disk availability; the final quota and MP4 checks remain in place.

After submission, ambiguous outcomes hold credits: a lost submit response, an interrupted submit, polling exhaustion, an over-age submitted job, a terminal provider failure with unknown billing semantics, an invalid output, or download/storage failure. The job becomes terminal `failed`, while its paid step becomes `needs_attention`; the executor derives the run's aggregate status, preserving concurrent sibling work.

### Provider decisions

These decisions are based on the information supplied by the current adapters, not an assumption about provider refund policies:

| Video provider | Definitive submit rejection | Failed/cancelled after acceptance |
| --- | --- | --- |
| fal | Refund using the shared rejection categories | Reconcile; normalized failure does not prove no billable work |
| Runware | Refund using the shared rejection categories | Reconcile; task errors do not establish charge reversal |
| Replicate | Refund using the shared rejection categories | Reconcile; failed/cancelled prediction alone is insufficient billing evidence |
| Runway | Refund using the shared rejection categories | Reconcile; `failureCode` or cancelled state alone does not establish no charge |
| Dola (experimental) | Refund using the shared rejection categories, including submit HTTP 402 | Reconcile; gateway failures have no reliable billing guarantee |

No adapter currently exposes a trusted post-acceptance “no billable work” signal. Consequently, no post-acceptance failure is automatically refunded based solely on a provider `failed` state. Provider integrations are unchanged.

### Text jobs

Successful text accounting and deterministic rejection refunds stay unchanged. Rate-limit rejections remain retryable, up to three attempts; exhausted rate limits refund. A lost response, timeout, network failure, invalid response, provider 5xx or reclaimed running job is uncertain and enters reconciliation instead of automatically sending another paid request. Empty or content-filtered output also needs reconciliation: these errors may occur after an HTTP 200 response and billable token generation. The previous retry/refund behavior for these ambiguous cases intentionally changes in this phase. Explicit deterministic input/auth/billing/configuration failures continue to refund.

Text responses have no durable remote job ID in the current normalized interface. Missing IDs/timestamps are displayed as unknown, not fabricated. Confirming such a step creates one usage event with one unit when actual usage is unavailable; credit pricing remains the frozen per-generation amount.

## Operator procedure

1. Sign in as a **system admin** and open **Admin → Reconciliation**. Workspace owners do not have accounting controls.
2. Review the pending item's workspace/owner, workflow/run/step/job IDs, model, reserved credits, remote ID, known submission success, job stage, submission and polling times, last known provider state, error and asset existence. Legacy incidents may lack timestamps and error categories.
3. Check the provider dashboard or other authoritative evidence. Do not infer a charge from `needs_attention` or infer a free generation from `failed`. If evidence is insufficient, leave the item pending.
4. Choose **Confirm charge** if the paid generation should count. This keeps the already reserved credits consumed and finalizes usage once. It does not generate, download, attach media, or mark the step successful.
5. Choose **Refund credits** if the reservation should be returned. The confirmation dialog shows the exact amount. An optional note (maximum 1,000 characters) should describe the evidence; do not include credentials or signed URLs.
6. Confirm the dialog. Review the immutable decision in **History**, including the administrator, timestamp, amount and note.

Do not use an unrelated manual admin adjustment to settle these incidents. Historical unrelated adjustments cannot reliably be matched to a step; check them before deciding to avoid compensating the same incident twice.

## API and authorization

All routes use existing system-admin session authorization; mutation routes enforce the existing same-origin check.

| Route | Behavior |
| --- | --- |
| `GET /api/admin/reconciliation?status=pending&limit=50&offset=0` | Pending paid steps, `{items, total}`; default status is pending |
| `GET /api/admin/reconciliation?status=resolved&limit=50&offset=0` | Final decisions and operator history |
| `POST /api/admin/reconciliation/jobs/{job_id}/confirm-charge` | Body `{ "note": "optional evidence" }`; finalize this job's usage without another debit |
| `POST /api/admin/reconciliation/jobs/{job_id}/refund` | Same body; restore this job's exact reservation |
| `POST /api/admin/reconciliation/{step_id}/confirm-charge` | Older form for a step with one paid job; 409 for a step with several jobs |
| `POST /api/admin/reconciliation/{step_id}/refund` | Same |

Each item carries `job_id`, plus `scene_index` and `operation` for jobs that make one file per scene or image; the admin page decides by `job_id`.

Page size is 1–100. Both mutation routes accept `{}`. Successful actions return the current item. Repeating the same decision returns 200 with the original decision and note; the opposite decision returns 409. Missing steps return 404; non-admins return 403 and unauthenticated callers 401. Invalid quotes, ownership relationships, nonterminal jobs, existing contrary accounting and balance-limit violations return 409 without partial changes.

Only explicitly selected operator fields are returned. Prompts, raw provider responses, credentials, OAuth data, submission URLs and signed output URLs are never returned in this API. New worker metadata contains only normalized facts. Regular run responses omit private provider progress and retain a small reconciliation status/amount/time summary.

## Persistence and concurrency

Migration `0011_credit_reconciliation` adds `credit_reconciliations`; it does not rewrite existing data. Each row links a step, job, original reservation ledger entry, final decision, credits, administrator, time and note. The step primary key and unique job/reservation constraints prevent multiple decisions for one operation. Allowed decisions and positive credits are constrained by the database. There are no history editing/deletion API routes; the application only inserts decision rows and never updates past ledger entries.

Migration `0012_job_reconciliation` moves the primary key from `step_id` to `job_id` (keeping an index on `step_id`), so a step with several paid jobs has one decision per job. Existing rows keep their data; each was already tied to its step's single job.

Pending is derived per job:

- a failed paid job whose own record in `output["jobs"]` is `needs_attention`, for steps with several jobs;
- otherwise, a failed job whose step is `needs_attention`.

An uncertain job can therefore be decided while its sibling jobs still run. The step leaves `needs_attention` once no job waits for a decision and none was confirmed as charged. The step-level `reconciliation` summary appears once no job is waiting. Old incidents work without a speculative backfill. Rows without matching paid reservations/relationships cannot be resolved through this API; inspect corrupted data separately.

Resolution locks the run, validates job/step/workspace/project/workflow relationships and the original debit, then locks the credit account. SQLite uses a write-locking no-op run update; PostgreSQL takes a row lock on the same run. Accounting, usage, decision and step/run state changes commit together. Existing usage/refund references are checked for matching ownership and amount. Same-action replays never update the original note, and conflicting actions cannot reverse a final decision.

Structured `reconciliation_charge_confirmed` / `reconciliation_refunded` events are emitted after commit with `admin_user_id`, `workspace_id`, `run_id`, `step_id`, `job_id`, `credits` and `provider`. The database decision is the durable audit record; logging is not a second accounting system.

## Run state and retry

- **Refund:** the step becomes `failed`. The run is recomputed from all steps and may remain running or need attention if siblings still require work. When retry is safe, the existing retry route creates a new run, new step/job IDs and a new reservation. It freezes the original request settings but never reuses a remote submission.
- **Confirm charge:** the step remains `needs_attention` with `reconciliation.status = confirmed_charge`. It is absent from Pending and visible in History. No valid asset or success is invented. The regular UI explains that the operator confirmed consumption; retry of that historical run is blocked. The user may explicitly start a new generation.
- Any unresolved or confirmed-charge step blocks retry, even if another step caused a failed aggregate run. A refunded run with generated assets or an approved review cannot be retried; start a new run deliberately instead.

There is no automatic media recovery, cancellation, provider billing lookup, historical decision reversal or remote job resubmission in this phase.

## Validation and limits

Run `python -m unittest discover -s tests -v`, then `npm run typecheck` and `npm run build` from `frontend/`. Tests use disposable databases and mocked providers. Live smoke tests remain opt-in and are not part of reconciliation verification.

Verified on 2026-09-30: 274 backend tests, 5 skipped, no failures; frontend typecheck and production build passed. The skipped tests require live-provider opt-in, a disposable PostgreSQL database or unavailable Windows symlink privileges. Migration tests compare every existing table's records across upgrade, including historical usage, jobs, assets and publications.

### Changed files

- Backend: `app/main.py`, `app/models.py`, `app/reconciliation.py`, `app/provider_progress.py`, `app/video_worker.py`, `app/text_worker.py`.
- Migration: `migrations/versions/0011_credit_reconciliation.py`.
- Tests: `tests/test_reconciliation.py`, `tests/test_reconciliation_workers.py`, `tests/test_text_nodes.py`.
- Frontend: `frontend/src/app/admin/page.tsx`, `frontend/src/components/reelforge/reconciliation.tsx`, `frontend/src/components/workflow/workflow-editor.tsx`, `frontend/src/lib/types.ts`, `frontend/src/lib/queries.ts`, and `frontend/src/lib/i18n/{vi,en,ja}.ts`.
- Documentation: this guide, `docs/IMPLEMENTATION_STATUS.md`, `docs/LIVE_PROVIDER_SMOKE_TEST.md`, `README.md`, and `docs/superpowers/plans/2026-09-30-credit-reconciliation.md`.

Provider billing still requires operator evidence. Storage checks are advisory and races/disk failures remain possible. Old incidents cannot gain missing provider facts retrospectively. SQLite tests exercise concurrent duplicate and opposite decisions; production PostgreSQL behavior requires PostgreSQL validation when a disposable test database is available. This change does not migrate the configured live database automatically: deploy migration 0011 before starting the updated API/workers.
