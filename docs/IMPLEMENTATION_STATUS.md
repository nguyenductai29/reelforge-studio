# ReelForge Studio Implementation Status

> Audit snapshot: branch `feat/studio-foundation`, commit `eb00d8a`, 2026-09-30.
> Updated the same day for Phase 1, the generic workflow execution engine in `app/workflow/` (see [Phase 1 changes](#phase-1-changes)).
> Line numbers drift, so items anchor on file and function names.

Each item uses the same fields:

- **Files**: where the behavior lives.
- **Current**: what the code does today.
- **Missing**: what it does not do yet.
- **Depends on**: features that must exist first, or that are waiting on this one. IDs such as `F3`, `P6` and `M1` refer to items in this document.

## Architecture Overview

### Components

| Component | Entry point | Role |
| --- | --- | --- |
| API | `app/main.py` (FastAPI, 45 `/api` routes) | Auth, workspace scoping, projects, workflows, runs, assets, AI tool catalog, settings, billing, admin, YouTube OAuth and publications. Run endpoints delegate to the workflow executor. On import, it checks that the DB is migrated and seeds settings. |
| Database bootstrap | `app/db.py`, `instance/bootstrap.json` | Builds the SQLAlchemy engine from `database_url` (PostgreSQL/psycopg in production, SQLite in tests). |
| Durable queue | `app/jobs.py`, `workflow_jobs` table | Idempotent enqueue by `logical_key` and lease-fenced claim, complete and fail. Uses `FOR UPDATE SKIP LOCKED` on PostgreSQL and an atomic `UPDATE … RETURNING` on SQLite. |
| Workflow engine | `app/workflow/` (`executor.py`, `registry.py`, `context.py`, `results.py`, `graph.py`, `nodes/`) | Evaluates a run's graph in topological order. The registry maps each node type to one handler; handlers resolve their inputs from parent outputs and return a standard result. Long work is queued as a durable job (F10). |
| Video worker | `app/video_worker.py` (`python -m app.video_worker`) | Claims `video:*` jobs, then submits, polls, downloads and stores a private MP4 asset. It records usage and reports the finished step to the executor, which continues the run. |
| Video providers | `app/providers/{fal,runware,replicate,runway,dola}.py`, `app/providers/catalog.py` | One text-to-video model per adapter. Each adapter validates the request and checks provider and media URLs (SSRF guard). The catalog is the single provider map (module, client, credential) shared by the API, the video handler and the worker. |
| YouTube worker | `app/youtube_worker.py` (`python -m app.youtube_worker`) | Claims `publish:youtube:*` jobs, refreshes OAuth tokens and runs resumable private uploads. |
| Publishing | `app/publications.py`, `app/publishers/*` | Publication records with a channel-generic schema, plus YouTube OAuth and upload. The Facebook and TikTok adapters exist only as libraries. |
| Billing and credits | `app/billing.py`, `app/payments.py`, `app/usage.py` | payOS VNQR checkout, webhook and refresh reconciliation, and the credit ledger. |
| Maintenance | `app/media_maintenance.py` | Cleans up stale `.part` downloads. Dry-run by default. |
| Frontend | `frontend/` (Next.js 15, React 19, React Query, `@xyflow/react`, Radix UI, Tailwind v4) | `next.config.ts` rewrites `/api/*` to the API. UI in vi/en/ja, following the workflow-first redesign. |

### Data model

Stored in PostgreSQL through SQLAlchemy 2, with Alembic migrations 0001–0010.

- **Tenancy:** `users`, `login_sessions`, `workspaces`, `memberships(role)`, `workspace_settings`, `system_settings`, `auth_login_attempts`.
- **Content:** `projects`, `assets` (with lineage `project_id`, `run_id`, `step_id`, `provider`, `model`), `workflows` (graph JSON in `definition`).
- **Execution:** `workflow_runs` (with `graph_snapshot`), `workflow_run_steps` (per-node `status`, `detail` and JSON `output`), `workflow_jobs`.
- **Money:** `plans`, `subscriptions`, `payment_orders`, `credit_accounts`, `credit_ledger`, `usage_events`.
- **AI configuration:** `ai_tools`.
- **Publishing:** `youtube_oauth_states`, `youtube_connections`, `publications`.

Most models live in `app/models.py`. Three are defined elsewhere: `AuthAttempt` in `app/auth_security.py`, `YouTubeOAuthState` and `YouTubeConnection` in `app/publishers/google_oauth.py`, and `Publication` in `app/publications.py`.

### The one end-to-end path that works today

```text
Project (title/topic)
  └─ POST /api/workflows/{id}/runs ─ persist_run()
       validate graph → snapshot → WorkflowExecutor.start_run()
         each node, in topological order: parents all completed? → registry handler → NodeExecutionResult
         idea/assets: completed · video: credits reserved, job queued · others: blocked · descendants: skipped
       steps, credit hold and job are committed in one DB transaction
  └─ video_worker.run_one()
       submit → poll every 10 s → download → validate MP4 → Asset + UsageEvent
       WorkflowExecutor.finish_step() → advance_run(): review → awaiting_review
  └─ POST /api/workflow-runs/{id}/approve     review completed → advance_run() · project.status = approved
  └─ POST /api/youtube/publications          Publication + job
  └─ youtube_worker.run_one()                private resumable upload → remote video ID
```

The editor saves and displays every other node type (script, scenes, image, voice, music, subtitle, render, publish). Each has a registered placeholder handler that blocks the step with a reason; none of them does real work yet.

### Phase 1 changes

Phase 1 replaced `app/workflow_engine.py` and the run logic in `app/main.py` with the modular engine in `app/workflow/` (F10). No database migration and no API shape change. Existing definitions, run snapshots and runs in flight keep working (covered by `tests/test_workflow_engine.py`).

Moved code:

- `persist_run`, the readiness checks and the review approval now call the executor and node handlers. `app/main.py` no longer contains per-node-type logic.
- The provider map, `video_provider_config_issue`, `video_credit_cost` and related helpers moved to `app/providers/catalog.py`. `app.main` still re-exports `video_provider_config_issue`.
- `NODE_TYPES` in `app/main.py` is now the set of registered node types.

Behavior that intentionally changed:

- When a run advances after the video finishes, each downstream node runs its own handler. Before, every non-review node became `blocked` with "no executor". Now `idea`/`assets` downstream of a video complete, and AI placeholders show their tool-based message.
- Approving a review now advances the steps after it. For example, a `publish` node after `review` becomes `blocked` instead of staying `skipped`. The run status is the same as before (`completed` or `blocked`).
- A handler that raises an unexpected exception fails its own step (`failed`, detail "Bước gặp lỗi khi thực thi.", `output.error.code = "handler_error"`) and the exception is logged. Its dependents stay `skipped`, and the run becomes `failed`, which the existing retry endpoint accepts. Before, the request returned 500. Run-rejecting errors (HTTP 400/402 for the video step) still roll back the whole request.
- An unknown node type (possible only in an old snapshot) blocks with "Loại bước này chưa được hỗ trợ." and `output.error.code = "unsupported_node_type"`, instead of the generic "no executor" message.
- Readiness `credits_required` is the sum of each handler's quote, so an (unsupported) graph with two video nodes now reports twice the quote. `VIDEO_CREDITS_PER_CLIP` is read only when the graph contains a video node.
- A completed video step's output also lists `asset_ids`, next to the existing `asset_id`.

### Configuration sources

- **`instance/bootstrap.json`:** `database_url` and optional `payos` credentials.
- **`system_settings` table:** `frontend_origin`, `secure_cookies`, `storage_dir`, `trial_project_limit`, `registration_enabled`.
- **`workspace_settings` table:** `default_language`, `video_orientation`, `approval_required`.
- **Environment variables (API and workers):**
  - Provider keys: `FAL_KEY`, `RUNWARE_API_KEY`, `REPLICATE_API_TOKEN`, `RUNWAYML_API_SECRET`, `DOLA_API_KEY`.
  - Provider settings: `DOLA_EXPERIMENTAL_ENABLED`, `DOLA_BASE_URL`, `DOLA_MEDIA_BASE_URL`, `DOLA_MAX_JOB_AGE_SECONDS`, `RUNWAY_OUTPUT_HOSTS`.
  - Limits: `VIDEO_CREDITS_PER_CLIP`, `VIDEO_JOB_MAX_AGE_SECONDS`, `WORKSPACE_MEDIA_QUOTA_BYTES`.
  - YouTube OAuth: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, `REELFORGE_TOKEN_ENCRYPTION_KEY`.
  - Tests only: `REELFORGE_TEST_DATABASE_URL`.
- **`frontend/instance/config.json`:** `api_base_url`.
- **Deployment:** systemd units for the API, frontend, video worker and YouTube worker (`docs/home-server-deployment.md`, `deploy.sh`).

### Where each audited area is covered

| # | Area | Status | Items |
| --- | --- | --- | --- |
| 1 | Workflow graph persistence | Fully implemented for topology and layout; per-node config missing | F1, M1 |
| 2 | Workflow execution engine | Fully implemented (modular executor and registry); run semantics beyond one clip are partial | F10, P1 |
| 3 | Node types | Partial (all 12 registered; 4 do real work) | P2, U2, U3 |
| 4 | Job architecture | Fully implemented (core) | F3 |
| 5 | Video generation providers | Partial | P3 |
| 6 | Asset/media persistence | Partial | P4 |
| 7 | AI tool/provider configuration | Partial | P5 |
| 8 | Credits reservation and usage tracking | Partial | P6, M6, R1 |
| 9 | Publishing architecture | Partial | P7, M3 |
| 10 | YouTube integration | Fully implemented (private uploads) | F4 |
| 11 | Frontend API integration | Fully implemented | F7 |
| 12 | Workflow node status rendering | Fully implemented (polling) | F8 |
| 13 | Tests | Partial | P8 |
| 14 | Migrations | Fully implemented | F9 |
| 15 | Error handling | Partial | P9 |
| 16 | Retry behavior | Partial | P10 |
| 17 | Security boundaries | Partial | P11, R2–R4 |
| 18 | Workspace isolation | Fully implemented (single-member model) | F2, M8 |

## Fully Implemented

### F1. Workflow graph persistence (topology and layout)

- **Files:**
  - `app/main.py`: `GraphNode`, `GraphEdge`, `WorkflowGraph`, `NODE_TYPES`, `validate_graph`, `parse_graph`, `default_graph`, `create_workflow`, `update_workflow`.
  - `app/models.py`: `Workflow.definition`.
  - Frontend: `frontend/src/components/workflow/workflow-editor.tsx` (`save`) and `frontend/src/lib/hooks.ts` (`useCreateFromTemplate`).
- **Current:**
  - **Storage:** the graph is JSON text in `workflows.definition`. Nodes are `{id, type, x, y, label}` and edges are `{source, target}`.
  - **Server validation:** 1–30 nodes and at most 60 edges, unique node IDs, 12 known node types, finite coordinates within ±100 000, no duplicate, self or dangling edges, and no cycles (Kahn's algorithm).
  - **Legacy data:** list-form definitions are converted on read.
  - **Defaults:** a new workflow starts as `idea → video → review`.
  - **Editor:** add, drag, connect (with a client-side cycle check), rename (`label`), duplicate, delete, undo/redo and explicit save. Templates create a workflow and then `PUT` their graph.
  - **Permissions:** only the workspace owner can save; the workflow count is limited by the plan.
- **Missing:**
  - Per-node configuration (see M1).
  - Rename and delete endpoints for workflows.
  - Conflict detection: the last save wins across tabs.
  - Version history for definitions. Runs do keep their own `graph_snapshot`.
  - A `GET /api/workflows/{id}` endpoint: the editor finds its workflow inside `/api/dashboard`.
- **Depends on:** nothing. P1 and M1 build on it.

### F2. Workspace isolation (query level)

- **Files:**
  - `app/main.py`: `authorize`, `workspace_for`, and the `workspace_id == ws.id` filter on every route.
  - `app/jobs.py` (`enqueue_job` ownership check), `app/publications.py` (`_approved_review`), `app/publishers/google_oauth.py` (membership and owner checks).
  - Tests: `tests/test_workflow_runs.py`, `tests/test_media_security.py`, `tests/test_youtube_routes.py`.
- **Current:**
  - Every read and write resolves the caller's workspace from the session cookie and filters by `workspace_id`. IDs from another workspace return 404.
  - Media files are stored at `<storage_dir>/<workspace_id>/<asset_id>`, where both IDs come from the DB, never from the user.
  - `enqueue_job` checks again that the step belongs to the run and the run to the workspace.
  - The OAuth callback is bound to the user who started it and to a workspace owner.
- **Missing:**
  - Multi-workspace membership. `workspace_for` takes the first membership row without ordering, so a user in two workspaces gets an arbitrary one.
  - Workspace switching, invites and non-owner roles. Only `owner` is ever created, so the member-role code paths are never exercised.
  - Database-level enforcement such as row-level security.
  - Admin routes are cross-workspace by design.
- **Depends on:** nothing. Team features (M8) are waiting on it.

### F3. Durable job queue

- **Files:** `app/jobs.py`, `app/models.py` (`WorkflowJob`), `migrations/versions/0007_jobs.py`, `tests/test_jobs.py`.
- **Current:**
  - **Enqueue:** `enqueue_job` is idempotent on `logical_key` (`INSERT … ON CONFLICT DO NOTHING`) and rejects a key reused with a different payload. The payload is stored as an immutable JSON snapshot.
  - **Claim:** `claim_due_jobs` leases jobs that are due or whose lease has expired. It increments `attempt_count` and issues a fresh `lease_token`.
  - **Finish:** `complete_job` and `fail_job` act only on the live lease (fencing) and are idempotent for the same token. `fail_job` either requeues with a delay or fails terminally.
  - **Routing:** workers select jobs by key prefix (`video:`, `publish:youtube:`).
- **Missing:**
  - Lease heartbeat or extension. Leases are fixed at 300 s for video and 900 s for YouTube.
  - A max-attempts or dead-letter policy in the queue itself. Each worker implements its own policy.
  - Cancellation, priorities, and concurrency limits per workspace or provider.
  - Metrics.
  - A meaningful retry count for video jobs: `attempt_count` increases on every poll.
- **Depends on:** nothing. P1, P3 and P7 depend on it.

### F4. YouTube integration (private uploads)

- **Files:**
  - Backend: `app/publishers/google_oauth.py`, `app/publishers/youtube.py`, `app/youtube_worker.py`, `app/publications.py`, and the `/api/youtube/*` routes in `app/main.py`.
  - Frontend: `frontend/src/app/channels/page.tsx`, `frontend/src/app/youtube/callback/page.tsx`, `frontend/src/components/reelforge/publish-dialog.tsx`, `frontend/src/app/publishing/page.tsx`.
  - Migrations `0009` and `0010`.
  - Tests: `test_google_oauth.py`, `test_youtube_upload.py`, `test_youtube_routes.py`, `test_youtube_publication_flow.py`, `test_youtube_worker_retry.py`.
- **Current:**
  - **Connecting:**
    - Only the workspace owner can connect. OAuth uses PKCE and a single-use state that expires after 10 minutes and is bound to the user and workspace.
    - A refresh token is required. Access and refresh tokens are Fernet-encrypted at rest.
    - A token refresh only succeeds if the stored ciphertext has not changed since it was read (compare-and-swap).
    - The connection's `connected_at` is frozen into each job, so a reconnect cannot redirect a queued upload to a different channel.
  - **What can be published:**
    - Only an MP4 asset produced by a completed video step of an approved run.
    - At most one publication per workspace, run and channel.
  - **Uploading:**
    - The worker saves the encrypted resumable session URL before sending any bytes, and resumes by probing the uploaded offset.
    - It uploads in 8 MiB chunks with `containsSyntheticMedia=true` and `privacyStatus=private`.
    - It checks that YouTube reports the video as private; anything else becomes `needs_attention`.
  - **Failures:**
    - Transient errors back off exponentially, for at most 6 attempts.
    - Uncertain outcomes become `needs_attention`.
    - A manual retry is allowed only when no media bytes could have been sent.
  - **UI:** connect and disconnect, a publish dialog that lists approved clips only, and a queue with state, error text, a YouTube Studio link and retry.
- **Missing:**
  - Verification against a real Google project and channel (listed as open in `docs/roadmap-ai-video-publishing.md`).
  - Public, unlisted or scheduled visibility, tags, category, thumbnail and playlists.
  - Revoking the Google grant on disconnect. Disconnect only deletes the local tokens.
  - Quota tracking.
  - More than one channel per workspace.
  - Publishing from the canvas `publish` node (see P7).
- **Depends on:** F3, and on P3 to produce an approved MP4.

### F5. Authentication and sessions

- **Files:** `app/main.py` (`setup`, `register`, `login`, `logout`, `authorize`, `hashed_password`), `app/auth_security.py`, `migrations/versions/0008_auth_security.py`, `frontend/src/components/reelforge/auth-screen.tsx`, `frontend/scripts/create-admin.mjs`, `tests/test_auth_security.py`.
- **Current:**
  - **Accounts:**
    - The first-user setup is serialized with a row lock. It creates a system admin and a "My Studio" workspace.
    - Self-registration on the trial plan can be switched on or off with `registration_enabled`.
    - Admins can also create accounts.
  - **Passwords:** scrypt (n=2^14) with a per-user salt, at least 12 characters.
  - **Sessions:**
    - A random token whose SHA-256 hash is stored in `login_sessions`.
    - It is sent as a 7-day `HttpOnly`, `SameSite=Strict` cookie; the `Secure` flag follows the `secure_cookies` setting.
    - Disabling a user deletes all their sessions.
  - **Login throttling:**
    - 5 failures within 15 minutes per `email|client-ip` block that identifier for 15 minutes.
    - Identifiers are stored only as hashes, and old rows are pruned in bounded batches.
- **Missing:**
  - Password change or reset and email verification.
  - 2FA (the UI shows "Sắp có").
  - Listing or revoking sessions, and session rotation.
  - Purging expired `login_sessions` rows.
- **Depends on:** nothing.

### F6. Subscription billing (payOS)

- **Files:**
  - Backend: `app/billing.py`, `app/payments.py`, and in `app/main.py` the `/api/billing*` and `/api/webhooks/payos` routes plus the admin plan and subscription routes.
  - Frontend: `frontend/src/app/billing/page.tsx`, `frontend/src/app/admin/page.tsx`.
  - Migrations `0002`–`0004`; `tests/test_checkout.py`.
- **Current:**
  - **Plans:** trial, standard and pro, each with project and workflow limits, `monthly_credits` and `price_vnd`.
  - **Checkout:** the owner's checkout creates a pending order, then a payOS link. An HTTPS origin is required except on localhost.
  - **Webhook:** the SDK verifies the signature, and the code checks currency and amount.
  - **Applying a payment:** `apply_paid` is idempotent.
    - A same-plan renewal adds 30 days to the later of now and the current end date.
    - `credits_award` is posted to the ledger once.
    - An order created before a later subscription change is marked `paid_unapplied`.
  - **Reconciliation:** a manual refresh checks pending orders against payOS.
  - **Admin tools:** edit plans, set subscriptions, adjust credits, and create or disable users.
- **Missing:**
  - Automatic monthly credit grants. Credits arrive only with a payment or an admin adjustment, and the seeded plans all have `monthly_credits = 0`.
  - Refunds, downgrades and proration; invoices.
  - Card payments (the UI shows "Sắp có").
  - A resolution flow for `paid_unapplied` orders.
  - Expiry reminders.
  - A configurable plan order: `billing_checkout` hard-codes trial < standard < pro.
- **Depends on:** P6 (credit ledger).

### F7. Frontend API integration

- **Files:** `frontend/src/lib/api.ts`, `frontend/src/lib/queries.ts`, `frontend/src/lib/types.ts`, `frontend/src/lib/errors.ts`, `frontend/next.config.ts`, `frontend/src/app/providers.tsx`.
- **Current:**
  - **Coverage:** every existing backend route is used: auth, dashboard, projects, workflows, runs (including readiness, approve and retry), asset upload and download, AI tools CRUD, settings, billing, usage, admin, and the YouTube connection and publications.
  - **Transport:** same-origin `fetch` through the Next.js rewrite, with typed responses.
  - **Caching:** React Query, polling every 5 s only while a run or upload is active. `useRefreshStudio` invalidates dependent queries when a run changes state.
  - **Errors:** an `ApiError` becomes a localized toast.
- **Missing:**
  - Calls for features the API lacks (delete, rename, cancel, pagination).
  - Generated types: `types.ts` is hand-written instead of generated from OpenAPI, so drift only shows up at runtime.
  - Pagination: `/api/dashboard` is the only source for projects, assets and workflows and returns them all at once (see D7).
  - Code-based error mapping: errors are translated by matching message text (see D6).
- **Depends on:** the backend routes listed above.

### F8. Workflow node status rendering

- **Files:**
  - `frontend/src/components/workflow/workflow-editor.tsx` (`statusOf`, `displayNodes`, `styledEdges`, run banner, `RunsSheet`).
  - `frontend/src/components/workflow/types.ts` (`runStatusToNode`, `readinessText`).
  - `frontend/src/components/workflow/studio-node.tsx`.
  - `frontend/src/lib/queries.ts` (`useRun`, `useReadiness`).
- **Current:**
  - **No run selected:** nodes show `ready` or `attention` from `GET /api/workflows/{id}/readiness`, evaluated for the selected video tool.
  - **Run selected** (via `?run=`, or after start or retry):
    - Each node shows its step status: `queued`, `submitting` (shown as running), `running`, `completed`, `awaiting_review` (shown as review), `blocked`, `skipped`, `failed`, and `needs_attention` (shown as failed).
    - Polls every 5 s while the run is `running`, `queued` or `submitting`.
    - Edges into running nodes are animated.
    - The video node previews the generated MP4, and the idea and assets nodes show their real step output.
    - The run banner offers approve, retry, download and a link to publishing.
    - The history sheet lists the last 30 runs.
- **Missing:**
  - Push updates (SSE or WebSocket); updates rely on polling only.
  - Progress or queue position. The worker drops fal's `queue_position`.
  - Nodes are matched to steps by the `node_id` of the current graph, not the run's `graph_snapshot`. Editing a graph while viewing an older run shows stale or idle nodes.
  - `needs_attention` looks the same as `failed`.
  - Readiness `runnable = false` does not stop a run from starting; the backend creates a `blocked` run instead.
- **Depends on:** P1.

### F9. Database migrations

- **Files:** `migrations/versions/0001_initial.py` … `0010_publications.py`, `migrations/env.py`, `alembic.ini`, `app/db.py`.
- **Current:**
  - A linear chain from 0001 to 0010, all with downgrades.
  - Data backfills for plans and subscriptions (0002) and credit accounts (0004).
  - 0007 uses batch `ALTER` so it also runs on SQLite.
  - `env.py` imports the tables defined outside `models.py`, so the metadata is complete.
  - The API refuses to start if `system_settings` is missing.
  - Offline SQL generation is disabled.
- **Missing:**
  - A model/migration drift check (`alembic check`) in CI.
  - The `uq_workflow_run_node (run_id, node_id)` constraint exists only in migration 0006, not on `WorkflowRunStep`.
  - `ON DELETE` rules on foreign keys. None are needed today because nothing is deleted, but M5 will need them.
- **Depends on:** nothing.

### F10. Generic node execution architecture (Phase 1)

- **Files:**
  - `app/workflow/executor.py`: `WorkflowExecutor.start_run`, `advance_run`, `finish_step`, `readiness`.
  - `app/workflow/registry.py`: `NodeRegistry`, `build_default_registry`, `default_registry`.
  - `app/workflow/context.py`: `ExecutionContext`, `RunOptions`, `NodeInputs`, `StepState`, `resolve_inputs`.
  - `app/workflow/results.py`: `NodeExecutionResult`, `NodeError`, `JobRequest`, `NodeReadiness`, `RunRequestError`, `derive_run_status`, and the status constants.
  - `app/workflow/graph.py`: `parse_graph`, `ordered_nodes`.
  - `app/workflow/nodes/`: `base.py` (`NodeHandler`), `idea.py`, `assets.py`, `video.py`, `review.py`, `pending.py`.
  - Callers: `app/main.py` (`persist_run`, `workflow_readiness`, `approve_workflow_run`), `app/video_worker.py` (`_store_result`).
  - Tests: `tests/test_workflow_engine.py`, plus the existing run, video and YouTube flow tests.
- **Current:**
  - **Registry:** each node type resolves to exactly one `NodeHandler`, and registering a type twice raises. An unregistered type resolves to `UnsupportedNodeHandler`, which blocks the step with a clear reason instead of crashing the run.
    - `idea`, `assets`, `video` and `review` have real handlers.
    - `script`, `image`, `voice` and `music` use `PendingAITaskHandler`; `scenes`, `subtitle`, `render` and `publish` use `PendingServiceHandler`. Both keep the existing messages.
    - The API accepts exactly the registered types (`NODE_TYPES`).
  - **Handler contract:** `execute(context, node, inputs)` returns a `NodeExecutionResult`, and `readiness(context, node)` returns a `NodeReadiness`.
    - `context` carries the database session, workspace, project, run, snapshot graph, run options (prompt override, tool ID, frozen video payload) and cached lookups (enabled tools, assets, workspace settings, credit balance).
    - Handlers never commit; everything happens in the caller's transaction.
  - **Standard result:**
    - Fields: `status`, `detail`, `output`, `metadata`, `error`, `job`, `job_id`, `asset_ids`.
    - Statuses reuse the stored values: generic *pending* is `skipped`, and *needs review* is `awaiting_review`. The model rejects unknown statuses, a `queued` result without a job, or a `failed` result without an error.
    - `output` is persisted together with `asset_ids` and `error.code`. `metadata` is not persisted. `job_id` is filled in after enqueue.
  - **Input resolution:** `resolve_inputs` gives each node its own `config` (read from the snapshot node if present), the status and output of each direct parent, `value(key)`, `outputs(node_type)`, and `asset_ids` created upstream. A node runs only when every parent has completed; otherwise it stays `skipped`.
  - **Execution passes:**
    - `start_run` evaluates the snapshot in topological order, persists one step per node, enqueues `JobRequest`s once the step rows exist (key `<kind>:<run>:<step>`), and derives the run status.
    - `finish_step` records a worker's result and calls `advance_run`.
    - `advance_run` re-evaluates only `skipped` steps whose parents are now all completed. It never rewrites steps that already left the pending state.
    - The run status comes from `derive_run_status`: any active step → `running`; then `awaiting_review`, `needs_attention`, `failed`, all completed → `completed`; otherwise `blocked`.
  - **Errors:**
    - A handler exception fails only that step (`handler_error`, logged); its dependents stay `skipped`.
    - `RunRequestError` (for example 402, not enough credits) rolls back the whole run request with the same HTTP status and message as before.
    - Worker failure paths (`_terminal_failure`) and retry rules are unchanged (P10).
  - **Compatibility:** no migration. Legacy list definitions, snapshots without labels, and runs already in flight from the previous engine all continue (tested).
- **Missing:** see P1 for run semantics that are still limited, and M1 for saving `config` through the API.
- **Depends on:** F1, F3. P1, P2, M1 and M2 build on it.

## Partially Implemented

### P1. Workflow execution (run semantics beyond the single-clip path)

- **Files:** `app/workflow/executor.py`, `app/workflow/nodes/video.py`, `app/workflow/nodes/review.py`, `app/main.py` (`start_workflow_run`, `approve_workflow_run`, `retry_workflow_run`), `app/video_worker.py`.
- **Current:**
  - Run creation, advancing after a job, approval and retry all go through the executor (F10).
  - After approval, the steps after `review` are evaluated too. For example, a `publish` node becomes `blocked` by its placeholder.
  - Retry creates a new run from the original snapshot and the frozen video payload. It is allowed only for `blocked` or `failed` runs, and never after approval.
- **Missing:**
  - Only the video handler queues jobs, and only the video worker reports results through `finish_step`. There is no generic worker loop for other job kinds yet.
  - At most one video node per workflow: the credit reservation reference is `reserve:<run_id>`, and readiness returns `unsupported_graph`.
  - The video prompt still comes from the run request or the project topic. No handler reads upstream outputs or node `config` yet; only test handlers do.
  - Run cancellation and re-running a single step.
  - `approval_required = false` has no effect (U6).
  - Run-level timeouts beyond the video job age limit.
  - Running independent branches in parallel. Each pass is sequential within one transaction.
- **Depends on:** F10, F3. New executors need per-node configuration (M1).

### P2. Node types

- **Files:** `app/workflow/registry.py` (`build_default_registry`), `app/workflow/nodes/*.py`, `app/main.py` (`NODE_TYPES`), `frontend/src/lib/workflow.ts` (`kindOf`, `nodeLibrary`, `EXECUTABLE`, `workflowTemplates`), `frontend/src/components/workflow/studio-node.tsx`.
- **Current:** all 12 node types the API accepts are registered. Only four of them do real work:

  | Type | Handler | Backend behavior | Notes |
  | --- | --- | --- | --- |
  | `idea` | `IdeaNodeHandler` | Completes locally | Output is `{title, topic}` |
  | `assets` | `AssetsNodeHandler` | Completes locally | Output lists every workspace asset |
  | `video` | `VideoNodeHandler` | Durable provider job | At most one per workflow |
  | `review` | `ReviewNodeHandler` | `awaiting_review` when a parent created media, then the approve endpoint; otherwise `blocked` | Always manual |
  | `publish` | `PendingServiceHandler` | Always `blocked` | Publishing is a separate manual flow (P7) |
  | `scenes`, `subtitle`, `render` | `PendingServiceHandler` | Always `blocked` | "No executor" |
  | `script`, `image`, `voice`, `music` | `PendingAITaskHandler` | Always `blocked` | "Provider not connected" or "no AI tool selected" |
  | any other type | `UnsupportedNodeHandler` | `blocked`, `error.code = unsupported_node_type` | Only reachable from old snapshots |

  - **Library:** 60 entries. 12 map to backend types; the other 48 are "Sắp có" (U2).
  - **Templates:** 4 of 10 have runnable graphs. `social-video`, `youtube-short` and `tiktok-video` are all `idea → video → review`; the fourth is `blank`.
- **Missing:**
  - Real handlers for 8 types. Adding one means writing a `NodeHandler` subclass and registering it in place of its placeholder; `app/main.py` needs no change.
  - Input and output schemas per node type.
  - Type-compatibility checks on edges.
- **Depends on:** F10, P1, M1, P5.

### P3. Video generation providers

- **Files:** `app/providers/{fal,runware,replicate,runway,dola}.py`, `app/providers/catalog.py` (`VIDEO_PROVIDERS`, `video_provider_config_issue`, `video_request_defaults`, `video_credit_cost`), `app/workflow/nodes/video.py`, `app/video_worker.py`, tests `test_provider_*.py`, `test_video_worker.py`, `test_dola_integration.py`, `test_workflow_engine.py`.
- **Current:** five adapters, each with one allow-listed text-to-video model:

  | Provider | Model | Defaults ReelForge sends | Configuration |
  | --- | --- | --- | --- |
  | fal | `fal-ai/veo3.1/fast` | 8s, 720p, audio on | `FAL_KEY` |
  | Runware | `bytedance:seedance@2.5` | 8s, 720p, audio on | `RUNWARE_API_KEY` |
  | Replicate | `google/veo-3.1-fast` | 8s, 720p, audio on | `REPLICATE_API_TOKEN` |
  | Runway Dev | `gen4.5` | 8s, 720p, no audio | `RUNWAYML_API_SECRET`, `RUNWAY_OUTPUT_HOSTS` |
  | Dola (experimental gateway) | `seedance-2.0`, `seedance-2.5` | 10s, `auto` | `DOLA_API_KEY`, `DOLA_BASE_URL`, `DOLA_EXPERIMENTAL_ENABLED=1` |

  - **Requests:** each adapter checks aspect ratio, duration and resolution against the model's capabilities.
  - **Errors:** HTTP failures map to stable codes with a `retryable` flag. A network error during submit becomes `submission_unknown` and is never resubmitted automatically.
  - **URL safety:** status, result and media URLs must match the provider's hosts, and redirects are not followed.
  - **Storing the result:** the worker downloads to a `.part` file, checks the size (at most 100 MB) and the MP4 box structure (`ftyp`, `moov`, `mdat`), then records the asset.
- **Missing:**
  - A shared adapter interface. Each module re-declares `ProviderError`, `VideoRequest`, `Submission` and the other types.
  - User control over duration, resolution and audio; ReelForge always sends the defaults above.
  - Image-to-video and reference inputs.
  - Square output: `video_orientation = square` blocks the run.
  - More than one model per provider, and per-model pricing.
  - Webhooks: the worker only polls, every 10 s.
  - Checks with live credentials. All tests use fakes.
  - Codec and duration inspection (for example with ffprobe).
  - Cancelling a job on the provider side.
- **Depends on:** F3, P6. F4 depends on it.

### P4. Asset / media persistence

- **Files:**
  - `app/main.py`: `upload_asset`, `download_asset`, `media_signature_matches`, `workspace_media_quota`, `upload_preflight`.
  - `app/body_limit.py`, `app/video_worker.py` (`_store_result`), `app/media_maintenance.py`.
  - Frontend: `frontend/src/app/media/page.tsx`, `frontend/src/app/library/page.tsx`.
  - Tests: `test_body_limit.py`, `test_media_security.py`, `test_media_maintenance.py`.
- **Current:**
  - **Storage:** files live on the local filesystem under `storage_dir` (default `instance/media`).
  - **Upload:**
    - Session and plan are checked before the body is read.
    - The 100 MB limit is enforced on both `Content-Length` and the streamed bytes.
    - Only allow-listed MIME types are accepted, and the file's leading bytes must match the declared type.
    - Each workspace has a quota (default 1 GiB). Uploads lock the workspace row while checking it.
  - **Generated videos:** MP4s are linked to their project, run, step, provider and model.
  - **Download:** a workspace-scoped `FileResponse` with an attachment filename.
  - **Cleanup:** `python -m app.media_maintenance [--apply]` removes stale `.part` files only.
- **Missing:**
  - Deleting or renaming assets.
  - Linking an upload to a project ("Add to project" is "Sắp có").
  - Using uploads in workflows. Only the `assets` node lists them.
  - Media metadata (duration, dimensions, codec) and thumbnails.
  - An object storage or CDN option, and backups.
  - Retention and orphan cleanup for final files.
  - Graceful handling of a missing file: an asset row whose file is gone returns 500.
  - The quota sums DB rows and does not check actual disk usage.
- **Depends on:** F2. M2 (render, image and voice executors) and M5 depend on it.

### P5. AI tool / provider configuration

- **Files:** `app/main.py` (`AIToolInput`, `/api/ai-tools*`, `workflow_readiness`), `app/workflow/nodes/video.py` and `pending.py` (`readiness`), `app/providers/catalog.py`, `app/models.py` (`AITool`), `migrations/versions/0005_ai_tools.py`, `frontend/src/app/models/page.tsx`.
- **Current:**
  - **Catalog:** each workspace has rows of `{task, provider, model, is_enabled}`, where `task` is `script`, `image`, `video`, `voice` or `music`. Only the owner can create, edit or delete them.
  - **What can run:** only a `video` row whose provider is in `VIDEO_PROVIDERS` and whose model is in that adapter's `VIDEO_MODELS`.
  - **Readiness:** each node's handler reports why a workflow cannot run, such as `missing_tool`, `missing_key`, `missing_config`, `invalid_config`, `experimental_disabled`, `unsupported_model`, `unsupported_aspect`, `unsupported_graph`, `insufficient_credits` or `unsupported_node`.
  - **Credentials:** provider keys are environment variables set by the operator and shared by all workspaces.
  - **UI:** the models page offers five video presets and marks the other tasks "config only".
- **Missing:**
  - A provider/model registry exposed by the API, with capabilities, pricing and parameters. The frontend hard-codes the presets, and its "runnable" badge checks only the provider name, not the model.
  - Validation when a tool is saved. An unsupported model is accepted and fails only at readiness or run time.
  - Per-workspace credentials (bring your own key).
  - Providers for anything other than video (LLM, image, TTS, music).
  - Per-node model selection saved in the workflow. Today the model is chosen for each run.
- **Depends on:** nothing. M1 and M2 depend on it.

### P6. Credits reservation and usage tracking

- **Files:**
  - `app/usage.py`: `post_credit`, `consume`.
  - `app/workflow/nodes/video.py` (`VideoNodeHandler.execute` reserves credits) and `app/providers/catalog.py` (`video_credit_cost`).
  - `app/main.py`: `usage_overview`, `adjust_credits`.
  - `app/video_worker.py`: `_terminal_failure`, `_store_result`.
  - `app/payments.py`.
  - Frontend: `frontend/src/app/billing/page.tsx` and the credit widget in `frontend/src/components/reelforge/app-shell.tsx`.
- **Current:**
  - **Ledger:** one balance per workspace, updated under a row lock, plus an append-only ledger. Each entry has a unique `reference`, so posting the same entry twice has no effect.
  - **Reservation:** a video run reserves a flat `VIDEO_CREDITS_PER_CLIP` (default 10) as `reserve:<run_id>`, in the same transaction as the run and its job. If the balance is too low, the API returns HTTP 402.
  - **Refunds:** only when the provider definitely rejected the request before accepting it (`refund:<run_id>`, once).
  - **Charging:** the reservation itself is the charge. A successful run adds one `UsageEvent` (`video:<step_id>`).
  - **Retries:** a retry reserves again under the new run ID, at the original frozen price.
- **Missing:**
  - Refunds when the provider reports a failed generation, or when the submit outcome is unknown. Both end in `needs_attention` with the credits still held, and there is no endpoint or UI to resolve them (R1).
  - Pricing by model or duration.
  - Trial and monthly grants: a new self-registered workspace starts with 0 credits.
  - Credit expiry.
  - Showing reserved and settled amounts separately in the ledger UI.
  - `usage.consume()` is unused by the app; the worker writes `UsageEvent` directly.
- **Depends on:** F6. Every new paid executor will depend on it.

### P7. Publishing architecture

- **Files:** `app/publications.py`, `app/youtube_worker.py`, `app/publishers/{youtube,google_oauth,facebook,tiktok}.py`, `frontend/src/app/publishing/page.tsx`, `frontend/src/app/channels/page.tsx`, `frontend/src/app/calendar/page.tsx`, tests `test_publications.py`, `test_facebook_reels.py`, `test_tiktok_posting.py`.
- **Current:**
  - **Publication records:** a `publications` table that works for any channel.
    - States: `queued → uploading → succeeded | failed | needs_attention`.
    - One record per run and channel.
    - The resumable upload session is stored encrypted, and updates go through lease-fenced helpers.
  - **Channels:** only `youtube` is wired end to end (F4).
    - `facebook.py` (Page Reels start, transfer, finish and status) and `tiktok.py` (Content Posting inbox upload, chunk planning and status) are tested HTTP adapters.
    - Neither has OAuth, token storage, a worker, a route or UI.
  - **Triggering:** publishing starts manually from the Publishing page. The workflow `publish` node plays no part.
- **Missing:**
  - Executing the `publish` node, for example queueing the publication automatically after approval.
  - Facebook and TikTok OAuth, connection storage, workers and UI.
  - Scheduled publishing (the calendar is "Sắp có").
  - Metadata forms per channel.
  - Analytics.
  - Publishing one run to several channels at once.
- **Depends on:** F3 and F4, and P1 for publishing driven by the node.

### P8. Tests

- **Files:** `tests/` (25 modules, 168 tests), `.github/workflows/ci.yml`.
- **Current:**
  - **Unit tests:** jobs, providers, publishers, OAuth, the body limit, media maintenance, login throttling, and the workflow engine.
  - **Workflow engine tests** (`tests/test_workflow_engine.py`, 22 tests):
    - registry lookup, including the unknown-type fallback;
    - result invariants and run-status derivation;
    - input resolution from config and parent outputs;
    - simple local workflows and unsupported nodes;
    - a failed parent skipping its dependents while completed outputs stay intact;
    - the full `idea → video → review` lifecycle through the executor, including approval advancing later steps;
    - video request rejections and the frozen retry payload;
    - readiness per handler;
    - legacy list definitions, pre-executor runs in flight, and one HTTP-level compatibility test.
  - **Integration tests:** each copies `app/` and `migrations/` to a temp directory, migrates a SQLite database with Alembic, and drives the API through `TestClient` in a subprocess. They cover projects, runs, the video worker, Dola, the YouTube flow and checkout.
  - **CI:** runs the backend tests on Python 3.13, and the frontend typecheck and build on Node 20.
- **Results after Phase 1** (2026-09-30, Windows, Python 3.14.7, Node 22.23.2):
  - `python -m unittest discover -s tests -v` → **Ran 168 tests, OK (skipped=3).** Before Phase 1 the same command ran 146 tests, OK (skipped=3).
    - `PostgreSQLJobClaimTest.test_locked_job_is_skipped_by_another_worker` was skipped because `REELFORGE_TEST_DATABASE_URL` is not set.
    - `test_rejects_linked_root` and `test_symlinked_file_and_workspace_are_never_followed` were skipped because this Windows user cannot create symlinks.
    - One warning: Starlette's `TestClient` asks for `httpx2`, which is listed in `requirements-dev.txt`.
  - `npm run typecheck` → OK. `npm run build` → OK (Next.js 15.5.26; 23 app pages generated). The frontend's only change in Phase 1 is en/ja translations for two new step messages.
  - `npm audit` (first audit) reports 1 high and 1 moderate advisory, both from PostCSS pulled in by `next`. The fix requires Next 16.
- **Missing:**
  - PostgreSQL in CI. It is the production database, and its `SKIP LOCKED`, `FOR UPDATE` and `ON CONFLICT` code paths are untested.
  - Frontend unit, component and E2E tests (there is no test script).
  - Contract tests against live providers.
  - Tests for non-owner roles and for `PUT /api/settings/system`.
  - Load tests for polling.
- **Depends on:** nothing.

### P9. Error handling

- **Files:** `app/main.py`, `app/workflow/executor.py` (`_evaluate`), `app/workflow/results.py` (`NodeError`, `RunRequestError`), `app/providers/*.py`, `app/publishers/*.py`, `app/video_worker.py`, `app/youtube_worker.py`, `frontend/src/lib/errors.ts`, `frontend/src/components/workflow/types.ts`, `frontend/src/lib/i18n/*.ts`.
- **Current:**
  - **Provider and publisher errors:** typed, with a stable `code`, a `retryable` flag and `http_status`. They never include secrets or response bodies.
  - **Workers:** turn those errors into step or publication states with readable detail.
  - **Node errors:** a handler can return a `failed` result with a `NodeError(code, message, retryable)`, which is stored as `output.error`. An unexpected exception inside a handler becomes a `handler_error` failure on that step. It is logged through `logging.getLogger("app.workflow.executor")`, the first use of `logging` in `app/`.
  - **API:** `HTTPException` with English `detail` strings. Handlers raise `RunRequestError`, which the API turns into the same HTTP status and message.
  - **Readiness:** returns stable status codes, which the frontend localizes.
- **Missing:**
  - Stable error codes on HTTP responses. The frontend translates by exact text: English `detail` strings through `t.errors.server[...]` and Vietnamese step `detail` strings through `t.details[...]` (see D6).
  - Logging outside the executor, and a log configuration for the API and workers.
  - A global exception handler and request IDs.
  - Configuration errors that still return 500 instead of a clear message:
    - An invalid `WORKSPACE_MEDIA_QUOTA_BYTES`, or an invalid `VIDEO_CREDITS_PER_CLIP` during readiness, raises `RuntimeError`. During a run, the same `VIDEO_CREDITS_PER_CLIP` error now fails the video step instead.
    - An asset row whose file is missing on disk makes `FileResponse` fail.
  - A worker that keeps crashing has no backoff or alerting.
- **Depends on:** nothing.

### P10. Retry behavior

- **Files:** `app/main.py` (`retry_workflow_run`), `app/video_worker.py`, `app/youtube_worker.py` (`_transient_retry_delay`), `app/publications.py` (`can_retry_publication`, `retry_publication`), `app/main.py` (`refresh_payment_order`).
- **Current:**
  - **Runs:** `POST /api/workflow-runs/{id}/retry` creates a new run (with `retry_of_id`) from the original snapshot and frozen video payload. Only `blocked` or `failed` runs qualify, and never after approval.
  - **Video jobs:**
    - Polling requeues the job every 10 s.
    - Temporary status or result errors are retried until three errors happen in a row.
    - Submit is never retried automatically; an unknown outcome becomes `needs_attention`.
    - A job may live at most `VIDEO_JOB_MAX_AGE_SECONDS` (default 6 h); Dola jobs at most `DOLA_MAX_JOB_AGE_SECONDS`.
  - **YouTube:**
    - Exponential backoff from 15 s up to 1 h, for at most 6 attempts, reusing the resumable session.
    - A manual retry is allowed only when no bytes could have been sent.
  - **Payments:** a manual refresh reconciles pending orders.
- **Missing:**
  - Retrying a `needs_attention` run. It is not allowed, and nothing else resolves it (R1).
  - Retrying a single step.
  - A lease heartbeat: a download that takes longer than the 300 s lease is thrown away and redone.
  - Jitter in backoff.
  - Backoff for provider rate limits. A `rate_limited` response on submit counts as a definite rejection: credits are refunded, the run fails, and the user must retry by hand.
  - A separate retry counter: `attempt_count` counts polls and retries together.
- **Depends on:** F3.

### P11. Security boundaries

- **Files:** `app/main.py` (`same_origin`, `authorize`, `admin_for`, `upload_preflight`), `app/auth_security.py`, `app/body_limit.py`, `app/publishers/google_oauth.py`, `app/publications.py`, `app/providers/*.py`, `app/billing.py`.
- **Current:**
  - **Accounts and sessions:**
    - Passwords are hashed with scrypt, and only session token hashes are stored.
    - The session cookie is `HttpOnly` and `SameSite=Strict`.
    - Login is throttled, and the first-admin setup is serialized.
  - **Request checks:**
    - Every mutating route checks the `Origin` header against an allow-list (`same_origin`).
    - Uploads are authorized before the body is read, and are checked for size, type, file signature and quota.
  - **Roles:**
    - Owner-only: settings, AI tools, YouTube, saving workflows and approving runs.
    - Admin-only: system settings and the admin routes.
  - **Secrets:**
    - OAuth tokens and upload sessions are Fernet-encrypted with `REELFORGE_TOKEN_ENCRYPTION_KEY`.
    - Other secrets live in the bootstrap file or the environment, never in SQL.
  - **Outbound calls:** URLs for providers, YouTube, TikTok and Facebook must match allow-lists, and redirects are not followed.
  - **Payments:** payOS webhooks are checked for signature, amount and currency.
- **Missing:**
  - Mitigations for R2–R4.
  - Requests without an `Origin` header pass `same_origin`, so CSRF protection relies on `SameSite=Strict`.
  - Security headers (CSP, `X-Content-Type-Options`, frame options) on both the API and Next.js.
  - Purging expired sessions.
  - An audit log of admin actions. Credit adjustments only record a ledger reason.
  - Rotation for `REELFORGE_TOKEN_ENCRYPTION_KEY` (R10).
  - `config.example.json` contains a real-looking database hostname and username.
- **Depends on:** nothing.

## UI Only / Mocked

These screens or controls are disabled and labelled "Sắp có" (coming soon), or they save a value that nothing uses. A spot check of the redesigned screens found no fabricated metrics presented as real data.

### U1. AI tool pages

- **Files:** `frontend/src/app/ai/{writer,image,video,repurpose,movie-recap}/page.tsx`, `frontend/src/components/reelforge/ai-tool.tsx`.
- **Current:**
  - Full prototype layouts. The inputs keep local state only.
  - The submit buttons are disabled, with a "Sắp có" banner linking to Workflows.
  - `/ai/video` does show real recent runs of workflows that contain a video node, and the real enabled video tools. Its own generate form is still disabled.
- **Missing:** backend endpoints for standalone writing, image, repurpose or movie-recap generation. None exist.
- **Depends on:** P5 (non-video providers), plus M2 executors or dedicated endpoints.

### U2. Node library entries and templates without a backend type

- **Files:** `frontend/src/lib/workflow.ts` (`nodeLibrary`, `workflowTemplates`), `frontend/src/components/workflow/workflow-editor.tsx` (`NodeLibrary`).
- **Current:**
  - 48 of the 60 library items show as "Sắp có" and cannot be dragged. Examples: research, summarize, translate, storyboard, thumbnail, image-to-video, voice clone, audio mixer, crop, overlay, transition, timeline, TikTok, Facebook, Shorts, schedule, and the URL, YouTube and upload inputs.
  - 6 of the 10 templates show only a preview diagram: `youtube-video`, `movie-recap`, `movie-review`, `repurpose`, `article-to-video`, `product-video`.
- **Missing:** backend node types and executors for them.
- **Depends on:** M1, M2.

### U3. Previews for nodes that do not execute

- **Files:** `frontend/src/components/workflow/studio-node.tsx` (`Preview`).
- **Current:**
  - The voice and music waveform, the subtitle lines and the script placeholder are static decoration.
  - The publish node says "YouTube private", but it never runs.
  - The image and render previews stay empty. The video preview shows a real MP4 once its step outputs an `asset_id`, which only the video node does today.
- **Missing:** outputs from the corresponding executors.
- **Depends on:** M2.

### U4. Project brief and workspace editor

- **Files:** `frontend/src/app/workspace/[projectId]/page.tsx`, `frontend/src/app/projects/[projectId]/page.tsx`.
- **Current:**
  - Real: editing the title and topic (`PATCH /api/projects/{id}`), and the project's runs, videos and publications.
  - Disabled, "Sắp có": the goal, platform, format, language, tone and duration chips, the audience field, the AI rewrite actions, the storyboard tab, the assistant panel and the "latest script" section.
- **Missing:** schema fields for the project brief, script storage and an assistant backend.
- **Depends on:** M1, and M2 (script executor).

### U5. Placeholders in settings, channels, publishing, billing, library, media and calendar

- **Files:** `frontend/src/app/settings/page.tsx`, `frontend/src/app/channels/page.tsx`, `frontend/src/app/publishing/page.tsx`, `frontend/src/app/billing/page.tsx`, `frontend/src/app/library/page.tsx`, `frontend/src/app/media/page.tsx`, `frontend/src/app/calendar/page.tsx`.
- **Current:** these are disabled or marked "Sắp có":
  - Settings: teammates, profile name, 2FA, storage retention, auto-schedule and several preference switches.
  - Channels and Publishing: the TikTok, Facebook and Instagram cards.
  - Billing: card payment.
  - Library: the Scripts tab.
  - Media: "Add to project".
  - Calendar: scheduling. The calendar does list real publications.
- **Missing:** the backend features behind each control.
- **Depends on:** M3, M5, M7, M8.

### U6. The `approval_required` workspace setting

- **Files:** `app/main.py` (`WORKSPACE_DEFAULTS`, `WorkspaceSettingsInput`, `update_workspace_settings`), `frontend/src/app/settings/page.tsx`.
- **Current:** the owner can edit it and it is saved, but no code reads it. A review node always requires manual approval, and a graph without one never does.
- **Missing:** engine logic that honors it (for example, skipping or requiring review).
- **Depends on:** P1.

## Missing

### M1. Per-node configuration

- **Files to change:** `app/main.py` (`GraphNode`, `validate_graph`), `app/workflow/nodes/*.py` (handlers that read `inputs.config`), `frontend/src/components/workflow/workflow-editor.tsx` (`Inspector`, `RunDialog`), `frontend/src/lib/types.ts`.
- **Current:**
  - The engine side is ready. `resolve_inputs` passes a snapshot node's `config` object to its handler, and `persist_run` stores the raw node, so a saved `config` would reach both the snapshot and the handlers.
  - The API side is not. `GraphNode` has no `config` field, so `PUT /api/workflows/{id}` drops it, and no built-in handler reads it yet.
  - The video model and prompt are chosen in the Run dialog or the inspector, held only in React state, and sent with `POST /runs`.
  - The aspect ratio comes from the workspace settings.
- **Missing:**
  - Typed configuration per node, saved in the graph: prompt template, tool or model ID, duration, resolution, voice, language and so on.
  - Server-side validation for each node type. A natural place is an optional `validate_config` on `NodeHandler`.
  - Readiness and handlers that use the configuration.
- **Depends on:** P5, so there is a registry to validate against. M2 and later work on P1 depend on it.

### M2. Executors for script, scenes, image, voice, music, subtitle, render and publish

- **Files to change:** new handlers in `app/workflow/nodes/` registered in `app/workflow/registry.py`, a generalized worker (today `app/video_worker.py`) that reports through `WorkflowExecutor.finish_step`, and new provider modules.
- **Current:** all eight node types have placeholder handlers that always return `blocked`.
- **Missing:**
  - Script and scene generation with an LLM.
  - Image generation.
  - Text-to-speech and music.
  - Subtitles, generated from the script or with speech recognition.
  - FFmpeg render and compose: timeline, captions and audio mix.
  - A publish executor that queues a publication after approval.
  - A price and a job kind for every paid executor.
- **Depends on:**
  - M1 (per-node config).
  - F10 (done: registry, input resolution, job requests, `finish_step`), plus a generic worker loop for new job kinds (P1).
  - P5 (providers) and P6 (credits).
  - P4, for render inputs and outputs.

### M3. Scheduling and more channels

- **Files to change:** `app/publications.py`, new workers modeled on `app/youtube_worker.py`, `app/publishers/{facebook,tiktok}.py`, `frontend/src/app/{calendar,channels,publishing}/page.tsx`.
- **Current:** YouTube private upload only, published immediately.
- **Missing:**
  - Facebook and TikTok OAuth, connection tables, workers and UI.
  - A scheduled time (`available_at`) for publications, and a calendar UI to set it.
  - Metadata per channel.
- **Depends on:** P7, reusing the patterns from F4.

### M4. Run control

- **Files to change:** `app/jobs.py`, `app/main.py`, `app/video_worker.py`, `frontend/src/components/workflow/workflow-editor.tsx`.
- **Current:** a run can only be started, approved, or retried as a whole new run.
- **Missing:**
  - Cancelling a run or job, including on the provider side where the provider supports it.
  - Re-running a single step.
  - Run timeouts.
  - Concurrency limits per workspace.
- **Depends on:** F3, P1.

### M5. Content lifecycle

- **Files to change:** `app/main.py`, `app/models.py`, new migrations, `frontend/src/app/{projects,workflows,media}/…`.
- **Current:** projects, workflows, assets and runs can never be deleted. Workflows cannot be renamed. Uploads cannot be linked to a project.
- **Missing:**
  - Delete, rename and archive.
  - `ON DELETE` rules on foreign keys, file cleanup, and releasing quota.
  - Linking an uploaded asset to a project.
- **Depends on:** F9, P4.

### M6. Credit reconciliation and grants

- **Files to change:** `app/usage.py`, `app/video_worker.py`, `app/main.py` (admin routes), `frontend/src/app/admin/page.tsx`.
- **Current:** `needs_attention` runs keep their credits reserved. Credits arrive only through a payment or a manual admin adjustment.
- **Missing:**
  - An admin endpoint and UI to resolve `needs_attention` runs, by refunding or confirming the charge.
  - A policy for provider-reported failures.
  - A scheduled job for monthly and trial grants.
  - A pricing table per model.
- **Depends on:** P6.

### M7. Account security features

- **Files to change:** `app/main.py`, `app/auth_security.py`, new migrations, `frontend/src/app/settings/page.tsx`.
- **Current:** login, logout and admin-disable only.
- **Missing:** password change and reset (needs email delivery), email verification, 2FA, session management, and purging expired sessions.
- **Depends on:** F5, plus an email provider.

### M8. Teams

- **Files to change:** `app/main.py` (`workspace_for`, role checks), `app/models.py` (`Membership`), `frontend/src/components/reelforge/app-shell.tsx`, `frontend/src/app/settings/page.tsx`.
- **Current:** one owner per workspace. The first membership row wins.
- **Missing:**
  - Invites and roles other than `owner`.
  - A deterministic `workspace_for`, and workspace switching.
  - Permissions per role for spending credits and publishing.
- **Depends on:** F2.

### M9. Observability and operations

- **Files to change:** `app/*`, workers, deployment units.
- **Current:** no logging. The only visibility is through state in the DB.
- **Missing:**
  - Structured logs for the API and workers.
  - Metrics: queue depth, job latency, provider errors and spend.
  - Alerting and an admin view of jobs and the queue.
  - Automated backup and restore for the database and media.
- **Depends on:** nothing.

### M10. Frontend tests

- **Files to change:** `frontend/package.json` and new test files.
- **Current:** CI runs only typecheck and build.
- **Missing:**
  - Component tests for the editor's status mapping and run actions.
  - An E2E happy path (create → run → approve → publish) against a stubbed API.
- **Depends on:** nothing.

## Technical Debt

| ID | Debt | Where | Impact |
| --- | --- | --- | --- |
| D1 | One 1,202-line module (1,372 before Phase 1) holds 45 routes, business logic and settings access. Run and node logic moved to `app/workflow/` and provider wiring to `app/providers/catalog.py`. | `app/main.py` | Both workers still import `app.main` (for `media_root`, `MAX_UPLOAD` and the quota) and so run its import-time side effects. |
| D2 | Importing the modules has side effects: the migration check and settings seeding in `app.main`, and the DB URL resolution in `app.db` | `app/main.py` (module level), `app/db.py` | Any import (tests, workers, tooling) needs a configured, migrated database. |
| D3 | Every provider adapter re-declares the same types. Since Phase 1 the backend has one provider map, but the frontend repeats it. | `app/providers/*.py`; `app/providers/catalog.py`; the presets in `frontend/src/app/models/page.tsx` | Adding a provider still means editing the backend catalog and the frontend presets separately. |
| D4 | The frontend copies backend constants and hand-writes the API types | `frontend/src/lib/workflow.ts` (`NODE_TYPES`, `EXECUTABLE`, `MAX_NODES`), `frontend/src/lib/studio.ts` (`ACCEPTED_UPLOADS`, `MAX_UPLOAD_BYTES`), `frontend/src/lib/types.ts` | Drift goes unnoticed, since nothing is generated from OpenAPI. |
| D5 | Some models are defined outside `app/models.py` | `app/auth_security.py`, `app/publishers/google_oauth.py`, `app/publications.py` | Complete metadata depends on the imports in `migrations/env.py`. |
| D6 | The backend returns user-facing Vietnamese text, and the frontend translates it by exact-text lookup | `app/workflow/nodes/*.py`, `app/workflow/executor.py`, `app/main.py`, `app/video_worker.py`; `details` and `errors.server` in `frontend/src/lib/i18n/{vi,en,ja}.ts` | Rewording any backend message silently breaks its translation. `NodeError.code` now gives new failures a stable code, but the frontend does not use it yet. |
| D7 | Endpoints and step outputs are not paginated | `/api/dashboard` returns every project, asset and workflow (with graphs); the `assets` node output embeds every asset (`ExecutionContext.assets` now loads them only when an `assets` node runs) | Cost grows with workspace size for both page loads and runs. |
| D8 | `attempt_count` increases on every poll, and leases have no heartbeat | `app/jobs.py`, `app/video_worker.py` | Misleading counts, and long downloads get redone. |
| D9 | Uploads are spooled twice (middleware, then multipart parser), and the workspace row stays locked while the file is written | `app/body_limit.py`, `app/main.py` (`upload_asset`) | Twice the disk I/O, and concurrent uploads to one workspace run one at a time. |
| D10 | Settings are read on every request (`same_origin` opens its own DB session) | `app/main.py` (`same_origin`, `setting`) | An extra DB round trip on every mutation. |
| D11 | The legacy `workspaces.plan` column is kept in sync by hand, and the plan order is hard-coded | `app/main.py` (`billing_checkout`, `update_subscription`), `app/payments.py` | The two can disagree if one code path is missed. |
| D12 | The model and the migrations disagree (`uq_workflow_run_node`), and CI has no `alembic check` | `app/models.py`, `migrations/versions/0006_workflow_runs.py` | Autogenerate would propose dropping the constraint. |
| D13 | Two ways to record usage | `app/usage.py` `consume()` is used only by tests; the worker writes `UsageEvent` directly | Future executors may pick different paths. |
| D14 | Advisory in the frontend dependencies | `frontend/package-lock.json` (PostCSS via Next 15.5; `npm audit`: 1 high, 1 moderate) | Affects the build toolchain; the fix needs Next 16. |
| D15 | Toolchain versions differ between CI and dev | CI uses Python 3.13 and Node 20; this audit ran on Python 3.14 and Node 22. `TestClient` warns unless `httpx2` from `requirements-dev.txt` is installed. | Something can pass in one environment and fail in the other. |

## Critical Risks

### R1. Credits stay reserved on `needs_attention` video runs

- **Where:** `app/video_worker.py`, every call to `_terminal_failure(..., refund=False)`:
  - The provider reports that generation `failed`.
  - The submit outcome is unknown: a network error or 5xx on the POST, or the worker stopped while the step was `submitting`.
  - Polling failed with 3 errors in a row.
  - Downloading or validating the result failed.
  - The workspace media quota is full after the provider has already finished (`_store_result`).
- **Effect:**
  - The run and step become `needs_attention` and the reserved credits are kept.
  - The run cannot be retried: retry accepts only `blocked` or `failed`.
  - No endpoint or UI resolves it; an admin can only post a manual credit adjustment.
  - Ordinary provider failures, such as a content-moderation rejection or an outage, take this path.
- **Direction:** M6, plus checking the quota before submitting.

### R2. Anyone can claim the first admin account

`/api/setup` stays open until the first user exists, and `/api/status` reports `setup_required`. Whoever reaches a new deployment first becomes the system admin. `frontend/scripts/create-admin.mjs` exists, but a deployment must run it before the Cloudflare Tunnel is exposed.

### R3. Defaults are unsafe for Internet exposure

- `secure_cookies` defaults to `false`, so the session cookie is sent without the `Secure` flag.
- `frontend_origin` defaults to `http://localhost:3000`.
- A mutating request without an `Origin` header is accepted.
- No security headers are set.

A deployment must switch to an HTTPS origin and secure cookies before going public (see the checklist in `docs/home-server-deployment.md`).

### R4. Login throttling keys on a client IP that may be the proxy

- **Where:** `app/main.py` (`login`). The throttle identifier is `email|request.client.host`.
- **Problem:** requests go through the Next.js rewrite, and in production through Cloudflare Tunnel. Unless forwarded client addresses are trusted and passed along, the API may see the proxy's address for every user.
- **Effect if so:**
  - Anyone can lock a known email out for 15 minutes with 5 bad attempts.
  - An attacker using many source addresses is not limited per source.
- **Next step:** check what the API actually sees in the production topology.

### R5. PostgreSQL-specific code paths are untested

Production runs on PostgreSQL, but every test runs on SQLite. The PostgreSQL claim path (`FOR UPDATE SKIP LOCKED`), the row locks and the `ON CONFLICT` upserts are covered by a single test, and it is skipped unless `REELFORGE_TEST_DATABASE_URL` is set.

### R6. Media sits on one local disk with no backup and no deletion

- The code has no automated backup for the database or media.
- Nothing can be deleted, so a workspace that reaches its quota (default 1 GiB) can never upload again.
- After that point, new generations also fail after the provider has already charged, and the credits end up held as in R1.

### R7. Dola is an experimental gateway

`app/providers/dola.py` talks to an operator-run gateway that, per its docstring, "owns its browser profiles". Its reliability and terms-of-service compliance are outside this codebase's control. It is gated by `DOLA_EXPERIMENTAL_ENABLED=1` and should stay off in production until it has been reviewed.

### R8. YouTube platform constraints

- Uploads are private only.
- The Google app has not yet been verified with a real channel (per the roadmap).
- `videos.insert` costs a lot of quota compared with a default daily allowance, and one OAuth client is shared by every workspace. Check the current quota in Google Cloud Console before inviting users.

### R9. No observability

Apart from handler exceptions in the workflow executor, the API and workers log nothing, and no log configuration exists. Failures are mostly visible only as DB state. Users will notice a crashed worker, an expired provider key or a full disk before operators do.

### R10. No handling for loss or rotation of the encryption key

If `REELFORGE_TOKEN_ENCRYPTION_KEY` is lost or changed, no stored YouTube connection or resumable upload session can be decrypted. In-flight uploads become `needs_attention`. The code does not support key rotation (for example with `MultiFernet`).

## Recommended Implementation Order

Guiding rules:

- Keep the working path `topic → clip → review → YouTube` passing tests at every step.
- Fix the money and security risks before adding executors.
- Build the node-configuration and executor foundation once, then add node types on top of it.
- As each backend capability lands, replace the matching "Sắp có" state in the existing UI rather than redesigning it.

| Step | Work | Resolves | Depends on |
| --- | --- | --- | --- |
| 0 | Operational hardening: <br>• structured logging in the API and workers <br>• a PostgreSQL service in CI with `REELFORGE_TEST_DATABASE_URL` <br>• `alembic check` in CI <br>• an enforced production checklist: `create-admin` before exposure, HTTPS origin, `secure_cookies`, forwarded-IP handling for the throttle <br>• frontend smoke tests for the editor's status mapping | R2–R5, R9, D12, part of M10 | — |
| 1 | Credit reconciliation: <br>• an admin endpoint and UI to resolve `needs_attention` runs <br>• a refund policy for provider-reported failures <br>• a quota check before submitting <br>• a trial and monthly grant policy | R1, part of R6, M6 | 0 |
| 2 | Split `app/main.py` into routers and services without changing behavior <br>• remove the import-time side effects <br>• return stable error codes instead of translating by text | D1, D2, D6, P9 | 0 |
| 3 | Provider/model registry: <br>• one adapter protocol <br>• one registry used by the API and worker and exposed to the frontend (capabilities, defaults, price) <br>• validate AI tools when they are saved | D3, D4, P5 | 2 |
| 4 | Per-node configuration in the graph: <br>• a schema per node type with server validation <br>• included in the run snapshot <br>• move the video prompt and model into node config <br>• readiness reads it | M1 | 3 |
| 5 | Engine refactor. **Done in Phase 1 (F10):** executor, registry, handlers, input resolution, job requests, `finish_step`, advancing after approval. **Still open:** <br>• a generic worker loop for new job kinds <br>• honor `approval_required` <br>• run cancellation <br>• more than one paid node per run | P1, U6, M4 | 4, F3 |
| 6 | First new executor: an LLM `script` node that feeds the video prompt, followed by `scenes` | Part of M2, U4 | 5, 3, P6 pricing |
| 7 | Asset lifecycle: <br>• delete and rename <br>• link uploads to projects <br>• ffprobe metadata <br>• a storage abstraction <br>• backups | M5, P4, R6 | 0 |
| 8 | `image`, `voice`/`music` and `subtitle` executors, then the FFmpeg `render` node | M2, U3 | 5, 6, 7 |
| 9 | `publish` node executor (queues a publication after approval), then scheduling (`available_at` plus calendar), then TikTok and Facebook connections built on the existing adapters | P7, M3, U5 | 5, F4 |
| 10 | Teams, roles and workspace switching; account security (password reset, 2FA); full frontend E2E tests | M7, M8, M10 | 2 |

Phase 1 took the engine part of step 5 ahead of steps 2–4, because it did not depend on them. Per-node configuration (step 4) is now the main blocker for new executors. Steps 1–3 can run in parallel once step 0 is in place. Step 7 is independent of steps 3–6 and can start earlier if storage pressure appears in production. Apply to Google, TikTok and Meta for platform API access early, because approval timelines are outside the team's control.
