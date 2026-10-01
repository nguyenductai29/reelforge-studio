# ReelForge Studio Implementation Status

> Audit snapshot: branch `feat/studio-foundation`, commit `eb00d8a`, 2026-09-30.
> Updated the same day for Phase 1, the generic workflow execution engine in `app/workflow/` (see [Phase 1 changes](#phase-1-changes)), for Phase 2, the text AI provider layer and seven text nodes (see [Phase 2 changes](#phase-2-changes)), for Phase 3, typed ports that carry structured data along edges (see [Phase 3 changes](#phase-3-changes)), for Phase 3.5, the node configuration inspector (see [Phase 3.5 changes](#phase-35-changes)), and for Phase 3.6, runtime hardening and live smoke-test tooling (see [Phase 3.6 changes](#phase-36-changes) and [Live Provider Verification](#live-provider-verification)).
> Line numbers drift, so items anchor on file and function names.

> Phases 4 and 5 add AI image generation (Runway `gen4_image`) and multi-scene video (one clip per scene), with one job, one credit reservation and one reconciliation decision per image or clip; see [Phase 4 and 5 changes](#phase-4-and-5-changes), [IMAGE_GENERATION.md](IMAGE_GENERATION.md) and [MULTI_SCENE_VIDEO.md](MULTI_SCENE_VIDEO.md). The operator has since live-verified Runway `gen4_image` in scene mode, Runway `gen4.5` in multi-scene mode, `Idea → AI Writer → Scene Splitter → Image (3) + Video (3) → Review` and its credit accounting.

> Phases 6, 7 and 8 add text-to-speech (Gemini TTS, one narration per script or scene), local SRT/WebVTT subtitles, and an FFmpeg render that joins the clips, narration and burned-in subtitles into one final MP4 that Review previews and publishing prefers; see [Phase 6, 7 and 8 changes](#phase-6-7-and-8-changes), [VOICE_GENERATION.md](VOICE_GENERATION.md), [SUBTITLES.md](SUBTITLES.md) and [RENDERING.md](RENDERING.md). They use offline tests only; no paid call and no real FFmpeg run were made here, because FFmpeg is not installed on the development machine.

> Phase 9 turns these steps into one creator workflow and completes YouTube publishing: starter templates, a Metadata step, the Publish hand-off, run summaries with credits, and publication visibility and tags (migration 0013); see [Phase 9 changes](#phase-9-changes) and [SOCIAL_VIDEO_WORKFLOW.md](SOCIAL_VIDEO_WORKFLOW.md). Offline tests only; no paid call and no real upload.

> Phases 10–13 add content sources and transcription, Movie Recap with source-clip extraction, TikTok and Facebook publishing through their official APIs, scheduled publishing, and operations tooling (default models, worker heartbeats, admin job views, a stuck-work audit, storage and cleanup), with migration 0014; see [Phase 10, 11, 12 and 13 changes](#phase-10-11-12-and-13-changes), [CONTENT_SOURCES.md](CONTENT_SOURCES.md), [REPURPOSING.md](REPURPOSING.md), [MOVIE_RECAP.md](MOVIE_RECAP.md), [MULTI_PLATFORM_PUBLISHING.md](MULTI_PLATFORM_PUBLISHING.md), [SCHEDULING.md](SCHEDULING.md) and [OPERATIONS.md](OPERATIONS.md). Offline tests only: no paid call, no real TikTok, Facebook or YouTube post, no real FFmpeg run.

> Phases 14–16 add card payments (OnePAY) beside VietQR (payOS) through one provider abstraction and one settlement path, rebuild the admin console as server-paginated tables that fit the window, and remove every "coming soon" control by implementing it (Background Music, image slideshows, the Movie Review, Article → Video and Product Video templates, scripts in the Library, display name and content defaults) or hiding it (Instagram, voice clone, timeline and other advanced steps), with migration 0015; see [Phase 14, 15 and 16 changes](#phase-14-15-and-16-changes), [PAYMENTS.md](PAYMENTS.md) and [FINAL_PRODUCT_AUDIT.md](FINAL_PRODUCT_AUDIT.md). Offline tests only: no real OnePAY, payOS, TikTok, Facebook or YouTube call and no paid AI call.

> Phase 17 makes local storage safe for many studios: one configurable root (`REELFORGE_STORAGE_ROOT`), per-plan storage limits with warning levels and race-free enforcement, asset kinds, retention of intermediate media once a final video exists, a daily cleanup that only deletes verified files inside the root, and owner-confirmed deletion, with migration 0016; see [Phase 17 changes](#phase-17-changes) and [STORAGE.md](STORAGE.md). Offline tests only.

> Phase 18 adds a system-admin-only payment setup view (field names and configured/missing, never a secret; a read-only check), a realtime notification center (Server-Sent Events with a polling fallback), in-app customer support with an admin console, and a live-verification center (safe readiness checks and a manual checklist), with migration 0017; see [Phase 18 changes](#phase-18-changes), [NOTIFICATIONS.md](NOTIFICATIONS.md), [SUPPORT.md](SUPPORT.md) and [LIVE_VERIFICATION.md](LIVE_VERIFICATION.md). Offline tests only.

> Phase 19 lets a system admin configure payOS (VietQR) and OnePAY (cards) from the web UI. It adds a central configuration resolver (admin first, bootstrap/env as fallback), credentials encrypted at rest and never returned, an enabled switch that never strands pending orders, OnePAY Sandbox/Production/Advanced with a production confirmation, an audit trail and a per-gateway live checklist, with migration 0018; see [Phase 19 changes](#phase-19-changes) and [PAYMENTS.md](PAYMENTS.md). Offline tests only.

> Phase 3.7 adds credit reconciliation and `needs_attention` resolution; see [Phase 3.7 changes](#phase-37-changes) and [operator procedure](CREDIT_RECONCILIATION.md). Gemini text, Runway `gen4.5` and the full workflow were live-verified by the operator before this handoff; Phase 3.7 uses offline tests only.

Each item uses the same fields:

- **Files**: where the behavior lives.
- **Current**: what the code does today.
- **Missing**: what it does not do yet.
- **Depends on**: features that must exist first, or that are waiting on this one. IDs such as `F3`, `P6` and `M1` refer to items in this document.

## Architecture Overview

### Components

| Component | Entry point | Role |
| --- | --- | --- |
| API | `app/main.py` (FastAPI, 71 `/api` routes) | Auth, workspace scoping, projects, workflows, runs, assets, AI tool catalog, settings and default models, storage, billing, admin reconciliation and operations, YouTube/TikTok/Facebook OAuth, channels, publications and scheduling. Run endpoints delegate to the workflow executor. On import, it checks that the DB is migrated and seeds settings. |
| Database bootstrap | `app/db.py`, `instance/bootstrap.json` | Builds the SQLAlchemy engine from `database_url` (PostgreSQL/psycopg in production, SQLite in tests). |
| Durable queue | `app/jobs.py`, `workflow_jobs` table | Idempotent enqueue by `logical_key` and lease-fenced claim, complete and fail. Uses `FOR UPDATE SKIP LOCKED` on PostgreSQL and an atomic `UPDATE … RETURNING` on SQLite. |
| Workflow engine | `app/workflow/` (`executor.py`, `registry.py`, `context.py`, `ports.py`, `config.py`, `results.py`, `graph.py`, `nodes/`) | Evaluates a run's graph in topological order. The registry maps each node type to one handler, which declares typed input and output ports and its settings. Before each handler runs, the executor checks the node's settings and resolves its inputs from edges, config and project context; the handler returns a standard result. Long work is queued as a durable job (F10, F12, F13). |
| Video worker | `app/video_worker.py` (`python -m app.video_worker`) | Claims `video:*` jobs, then submits, polls, downloads and stores a private MP4 asset. A single clip is tracked on its step; clips made one per scene run through the shared child-job engine (Phase 5). It records usage and reports the finished step to the executor, which continues the run. |
| Image worker | `app/image_worker.py` (`python -m app.image_worker`) | Claims `image:*` jobs (one per image) and stores private PNG/JPEG/WEBP assets after checking their bytes (Phase 4). |
| Child-job engine | `app/media_jobs.py`, `app/workflow/nodes/media.py` | One paid job per image, scene clip or narration, each with its own credit references, and step settlement once every job has finished (Phases 4–6). Providers that answer with the file itself (text-to-speech) skip polling. |
| Voice worker | `app/voice_worker.py` (`python -m app.voice_worker`), `app/providers/voice/`, `app/audio_files.py` | Claims `voice:*` jobs (one per narration), calls Gemini TTS and stores checked WAV assets (Phase 6). |
| Subtitles | `app/subtitles.py`, `app/workflow/nodes/subtitle.py` | Deterministic cue timing and SRT/WebVTT files, written while the run advances; no worker (Phase 7). |
| Render worker | `app/render_worker.py` (`python -m app.render_worker`), `app/render.py` | Claims `render:*` jobs and runs system FFmpeg without a shell in a private folder, storing the final MP4 (Phase 8). Also cuts Movie Recap source clips (`clips.extract`, Phase 11). |
| Source worker | `app/source_worker.py` (`python -m app.source_worker`), `app/sources.py`, `app/providers/transcription/` | Claims `source:*` jobs: fetches public web pages with SSRF protection, and transcribes audio/video (FFmpeg audio extraction, OpenAI Whisper, timestamped segments) as a paid, reconcilable job (Phase 10). |
| Scene matching | `app/scene_matching.py`, `app/workflow/nodes/recap.py` | Local transcript-similarity matching of recap scenes to source moments (Phase 11). |
| Image providers | `app/providers/image/` (`base.py`, `runway.py`), `app/image_files.py` | Provider-neutral `ImageGenerationProvider`; Runway `gen4_image`; download and signature checks for image files. |
| Video providers | `app/providers/{fal,runware,replicate,runway,dola}.py`, `app/providers/catalog.py` | One text-to-video model per adapter. Each adapter validates the request and checks provider and media URLs (SSRF guard). The catalog is the single provider map (module, client, credential) shared by the API, the video handler and the worker. |
| Text providers | `app/providers/text/` (`base.py`, `openai.py`, `anthropic.py`, `gemini.py`) | One `TextGenerationProvider` interface; each adapter calls its vendor's HTTP API with `httpx` and returns a normalized `TextResult` (F11). |
| Text worker | `app/text_worker.py` (`python -m app.text_worker`) | Claims `text:*` jobs, calls the provider outside any DB transaction, settles or refunds credits, and reports the step to the executor, which continues the run. |
| YouTube worker | `app/youtube_worker.py` (`python -m app.youtube_worker`) | Claims `publish:youtube:*` jobs, refreshes OAuth tokens and runs resumable uploads (private by default; unlisted or public since Phase 9). |
| Social worker | `app/social_worker.py` (`python -m app.social_worker`), `app/publishers/{tiktok,facebook,channel_oauth}.py` | Claims `publish:tiktok:*` and `publish:facebook:*` jobs: TikTok inbox drafts and Facebook Page Reels, one saved step per claim (Phase 12). |
| Scheduler worker | `app/scheduler_worker.py` (`python -m app.scheduler_worker`) | Queues scheduled publications when due, under row locks; idempotent and restart-safe (Phase 13). |
| Worker health | `app/heartbeat.py`, `worker_heartbeats` table | Every worker reports a throttled heartbeat; admins see ok, stale, error or missing (Phase 13). |
| Publishing | `app/publications.py`, `app/publishers/*` | One publication per run and channel (YouTube, TikTok, Facebook) with per-channel metadata validation, scheduling, cancel and reschedule, and an independent upload job per channel. |
| Billing and credits | `app/billing.py`, `app/payments.py`, `app/usage.py` | payOS VNQR checkout, webhook and refresh reconciliation, and the credit ledger. |
| Maintenance | `app/media_maintenance.py` | Cleans up stale `.part` downloads, worker temp folders and (with `--orphans`) files without an asset row; reports storage per workspace. Dry-run by default. |
| Runtime support | `app/runtime_env.py`, `app/logs.py`, `app/provider_check.py`, `app/smoke_test.py`, `app/providers/errors.py`, `app/video_files.py` | One runtime environment file for every process, structured logs, the provider pre-flight, live smoke tests and run reports, shared error categories, and MP4 download and checks (Phase 3.6). |
| Frontend | `frontend/` (Next.js 15, React 19, React Query, `@xyflow/react`, Radix UI, Tailwind v4) | `next.config.ts` rewrites `/api/*` to the API. UI in vi/en/ja, following the workflow-first redesign. |

### Data model

Stored in PostgreSQL through SQLAlchemy 2, with Alembic migrations 0001–0014.

- **Tenancy:** `users`, `login_sessions`, `workspaces`, `memberships(role)`, `workspace_settings`, `system_settings`, `auth_login_attempts`.
- **Content:** `projects`, `assets` (with lineage `project_id`, `run_id`, `step_id`, `provider`, `model`, and `source_asset_id` for clips cut from a source video), `workflows` (graph JSON in `definition`).
- **Execution:** `workflow_runs` (with `graph_snapshot`), `workflow_run_steps` (per-node `status`, `detail` and JSON `output`), `workflow_jobs`.
- **Money:** `plans`, `subscriptions`, `payment_orders`, `credit_accounts`, `credit_ledger`, `usage_events`, `credit_reconciliations`.
- **AI configuration:** `ai_tools`.
- **Publishing:** `youtube_oauth_states`, `youtube_connections`, `channel_oauth_states`, `channel_connections` (TikTok, Facebook), `publications` (with `scheduled_for`, `published_at`).
- **Operations:** `worker_heartbeats`.

Most models live in `app/models.py`. Some are defined elsewhere: `AuthAttempt` in `app/auth_security.py`, `YouTubeOAuthState` and `YouTubeConnection` in `app/publishers/google_oauth.py`, `ChannelOAuthState` and `ChannelConnection` in `app/publishers/channel_oauth.py`, and `Publication` in `app/publications.py`.

### The end-to-end paths that work today

```text
Project (title/topic)
  └─ POST /api/workflows/{id}/runs ─ persist_run()
       validate graph → snapshot (edges name their ports) → WorkflowExecutor.start_run()
         each node, in topological order: parents all completed? → resolve typed inputs → required inputs present?
           → registry handler → NodeExecutionResult
         idea/assets/scenes: completed · video/text: credits reserved, job queued · others: blocked · descendants: skipped
       steps, credit holds and jobs are committed in one DB transaction
  └─ text_worker.run_one()                   (for each text step, as it becomes ready)
       provider.generate() → text + usage → UsageEvent (or refund on failure)
       WorkflowExecutor.finish_step() → advance_run(): the next text/image/video steps reserve credits and queue
  └─ image_worker.run_one()                  (one job per image: per scene, or 1–4 from a prompt)
       submit → poll → download → check PNG/JPEG/WEBP bytes → Asset + UsageEvent; the step settles when all jobs end
  └─ video_worker.run_one()                  (one job per clip: single, or one per scene)
       submit → poll every 10 s → download → validate MP4 → Asset + UsageEvent
  └─ voice_worker.run_one()                  (one job per narration: a script, or one per scene)
       generate → check WAV/MP3 bytes → Asset + UsageEvent; then Subtitle writes its SRT/VTT during advance_run()
  └─ render_worker.run_one()                 (one job per Render step, once clips, narration and subtitles exist)
       ffprobe → ffmpeg (concat, narration, burned subtitles) → validate MP4 → Asset
       WorkflowExecutor.finish_step() → advance_run(): review → awaiting_review
  └─ POST /api/workflow-runs/{id}/approve     review completed → advance_run() · project.status = approved
  └─ POST /api/youtube/publications          Publication + job
  └─ youtube_worker.run_one()                private resumable upload → remote video ID
```

The editor saves and displays the remaining node types (script, music, publish). Each has a registered placeholder handler that declares its future ports and blocks the step with a reason; none of them does real work yet.

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

### Phase 2 changes

Phase 2 added real text generation (F11). No database migration.

New code:

- `app/providers/text/`: a provider-neutral `TextGenerationProvider.generate(model, prompt, system_prompt, temperature, max_tokens, response_format)` returning `TextResult(text, usage, provider, model, raw_metadata)`, and OpenAI, Anthropic and Gemini adapters using direct HTTP.
- `app/workflow/nodes/text.py`: `ai_writer`, `summarize`, `rewrite`, `translate`, `hook`, `title` and `cta` handlers.
- `app/text_worker.py`: the worker for `text:` jobs.
- `jobs.live_lease`: a lease check shared by workers.

Engine and API changes:

- `start_run` creates every step row before calling handlers, so a handler can key credit holds on its own step (`context.step_for(node)`).
- `advance_run` and `finish_step` lock the run row, and the approve endpoint now does too. Two workers finishing sibling steps can no longer each miss the other's completion.
- `RunRequestError` now carries a `code` and a `step_detail`. When it is raised while a run is starting, the API still rejects the request (400/402). When a node only becomes ready later (for example, not enough credits for a downstream step), the executor blocks that step with the code instead of undoing the worker's transaction.
- `GraphNode` accepts an optional `config` object. The handler's `validate_config` checks it (unknown keys and bad values return 422), it is saved in the workflow and the run snapshot, and handlers receive it as `inputs.config`. The canvas keeps it when saving and duplicating nodes; it has no editing UI yet (M1).
- Readiness is `runnable` only when the balance also covers the sum of every node's credits.

Frontend changes, without layout changes:

- The seven AI library items became draggable and are no longer "Sắp có"; they map to the new node types.
- Text nodes show a short preview of the generated text on the canvas, and the full text in the inspector.
- The Models page offers Text presets for OpenAI, Anthropic and Gemini, and marks those tools as runnable.
- Run dialog: when a graph has credit costs but no video, it now shows the credit line.
- New vi/en/ja strings for the text nodes, their step messages and errors.

Deployment: a `reelforge-text-worker` unit (`docs/home-server-deployment.md`, `deploy.sh`).

### Phase 3 changes

Phase 3 made edges carry typed data between ports (F12). There is no database migration and no graph version field. Handles are optional, and older graphs are read tolerantly.

New code:

- `app/workflow/ports.py`: port and data-type definitions, edge binding with the legacy fallback, graph normalization, edge validation, and the port catalog.
- `resolve_node_inputs(node, graph, states, registry=…, context=…)` in `app/workflow/context.py`.
- `app/workflow/nodes/scenes.py`: the scene splitter.
- `GET /api/workflow-node-types`.

Engine and API changes:

- **Handler contract:** every handler declares `inputs`, `outputs` and `requires`. The executor resolves input values before calling a handler, and blocks the step (`missing_input`, with `output.missing_inputs`) when a required group has no value. Handlers no longer read their parents' outputs themselves.
- **Edges:** each edge can name `sourceHandle` and `targetHandle`; `source_handle` and `target_handle` are also accepted.
  - Saving checks that named ports exist, that the data types match, and that an input taking one connection gets only one.
  - Two nodes may now be joined by several edges, one per port pair.
- **Normalization:** stored definitions, API responses and run snapshots name the ports of every edge. Old edges get their default ports when they are read, saved or run. A snapshot whose ports no longer exist falls back to the default mapping instead of failing.
- **Input priority:** a connected edge beats the node's config, which beats the project context. For the AI Writer, a connected idea now wins over `config.prompt`. In Phase 2, the setting won.
- **Standard output keys:**
  - `ai_writer` → `script`, `summarize` → `summary`, `rewrite`/`translate` → `text`.
  - `hook`/`title`/`cta` → the first option under their own name, plus `text` (all options) and `options`.
  - `scenes` → `scenes`; `video` → `video_assets`.
  - Phase 2 outputs stored under `text` are still read.
- **`scenes` became a real node.** It splits a script locally, with no provider call and no credits, into `{index, text, visual_prompt, duration}` items.
- **Video prompt source,** in order: the Run dialog prompt, then a connected `prompt`, then connected `scenes` (joined as "Shot 1: … Shot 2: …"), then the project topic. Connected text is cut to 1,000 characters at a word boundary, which is Runway's limit.
- **Review** stores the media it waits on (`asset_ids`), and approval now merges into the review output instead of replacing it. Its `video_assets` output passes the approved clip on.

Frontend changes, without layout changes:

- **Canvas handles are ports:** each node lists its input ports on the left and output ports on the right, with labels (for example "Kịch bản ●", "● Cảnh").
  - While you drag, the canvas only accepts compatible ports. An incompatible drop shows "Hai cổng này không nối được với nhau."
  - Connecting to an input that takes one connection replaces the old edge.
  - The "→ output" footer text was removed, because the port labels replace it.
- **Editor:** the page waits for the port catalog before drawing the canvas, and saves `sourceHandle`/`targetHandle`.
- **Scenes:** the scenes node previews its scene count, and the inspector lists the scenes.
- **Inspector (advanced mode):** shows each incoming edge as "Prompt ← AI Writer · Script".
- **Run dialog:** the video prompt now starts empty, so connected inputs are used unless you type a prompt.
- **Library:** "Chia kịch bản thành cảnh" (`scenes`) is no longer "Sắp có".

### Phase 3.5 changes

Phase 3.5 made node settings editable in the existing inspector (F13). There is no database migration; settings stay in each node's `config`.

New code:

- `app/workflow/config.py`: `ConfigField` (type `select`, `integer`, `number`, `text` or `tool`, with default, options, presets, range, maximum length, `advanced` flag, label key and error `code`), `ConfigError`, `validate_config`, `config_values` and `check_tools`.
- `config_fields` on every executable handler, replacing the per-handler validator dictionaries of Phase 2.
- `video_duration_supported` in `app/providers/catalog.py`.
- Frontend: `frontend/src/components/workflow/node-config.ts` (the same checks as the backend) and `config-fields.tsx` (fields rendered from the schema).

API changes (additive except where noted):

- `GET /api/workflow-node-types` adds `config` (the field list) to each node type.
- **Changed:** saving invalid settings returns 422 whose `detail` is an object, `{code, field, node_id, message}`, instead of a string. Other 422s keep string details.
- Saving checks that a `tool_id` names one of the workspace's AI tools with the right task and a supported provider (`unsupported_model`). A disabled tool may be saved; readiness reports it.
- Readiness steps add `code` and `field`, and three statuses: `invalid_settings`, `missing_input` (a required input that no edge, setting or project topic can fill) and `tool_unavailable`.
- A run no longer validates settings up front. A node whose stored settings are invalid is blocked with the setting's code (`output.invalid_setting`, `output.error.code`), and the rest of the run continues.
- `POST /runs` still accepts `prompt` and `tool_id`, but a video step's own `prompt` and `tool_id` settings take precedence.
- **Fixed:** a retry froze the run's first job, which is a text job when a text branch starts beside the video; the video step of the retry then blocked. It now freezes the `video:` job.

Settings per node:

| Node | Settings (normal mode) | Advanced mode adds |
| --- | --- | --- |
| `ai_writer` | language (auto/vi/en/ja), tone (8), platform (5), target duration (30 s–10 min presets, 5–3600 s), brief, instructions, model | temperature, max tokens |
| `summarize` | length (short/medium/detailed), language, instructions, model | tone, platform, temperature, max tokens |
| `rewrite` | tone, platform, length (shorter/same/longer), instructions, model | language, temperature, max tokens |
| `translate` | target language (auto/vi/en/ja), model | instructions, temperature, max tokens |
| `hook` / `title` / `cta` | count (default 3 / 5 / 3), tone or title style, platform, model | language, instructions, and tone for `title`; temperature, max tokens |
| `scenes` | target scene duration (default 6 s, 2–60), maximum scenes (default 12, 1–20), visual style (6) | — |
| `video` | model, aspect ratio (auto/9:16/16:9), clip length (auto/4/6/8 s), prompt override (≤1,000 characters) | — |

Behavior that intentionally changed:

- **Stricter values:** tone, platform, language and target language accept only the listed codes. In Phase 2 they were free text or any language code. A stored value outside the list (possible only through the API) now blocks that node with its code instead of reaching the prompt.
- **Scene splitter:** defaults to 12 scenes (was 8) and splits any paragraph longer than the target duration at sentence ends. Before, only a script written as one paragraph was split, at about 16 seconds per scene. A visual style adds a short suffix such as "Cinematic style." to each `visual_prompt`.
- **Prompt wording:** tone and platform are sent as phrases ("YouTube Shorts (vertical, short)"); summaries get a length instruction and titles a style instruction.
- **Video:** the queued output also records `aspect_ratio` and `duration`.
- **Handlers without `config_fields`** now have any config rejected at run time as well as on save.

Frontend changes, without layout changes:

- **Inspector:** name and status, then **Connections** (each input port with what feeds it, and the output ports), **Configuration** (fields from the schema), **Result** (video, text or scenes), and the actions. Advanced mode adds the raw keys and port names, the provider and model ID, the model a step used, and the stored settings as JSON. No API keys are sent to the browser.
- **Editing:** a change marks the workflow unsaved. Choices are one undo step each, and typing in one field is one undo step. Duplicating a node copies its settings.
- **Validation:** an invalid value shows its message under the field, turns the node to "Cần thiết lập", and blocks **Save** with a toast that selects the step. A 422 from the API selects the step it names.
- **Run dialog:** the video model and prompt fields were removed (they are node settings now). The dialog lists the saved workflow's readiness problems and the credit estimate.
- **Readiness:** the new statuses are translated (vi/en/ja), and `invalid_settings` shows the message for its code.

### Phase 3.6 changes

Phase 3.6 added no product feature and no migration. It makes the existing pipeline testable against real providers and diagnosable when it fails.

- **Shared runtime environment (`app/runtime_env.py`):**
  - The API (lifespan), the text, video and YouTube workers (`main`), `app.provider_check` and `app.smoke_test` load `.env.runtime`, or the file named by `REELFORGE_ENV_FILE`.
  - Variables already set in the process win. Nothing loads on import, so tests never read the file.
  - `.env.runtime.example` lists the variable names only, and `.gitignore` ignores every other `.env*` file.
  - Production uses one `EnvironmentFile=/etc/reelforge/runtime.env` for every unit (deployment guide §4.1).
  - Each process logs `process_started` with the file it loaded and an 8-character SHA-256 fingerprint per provider key.
- **Pre-flight (`python -m app.provider_check`):**
  - For the selected text and video provider, it reports the provider, the model (recognized, accepted or unsupported), whether the key is configured, missing or invalid (with its fingerprint), provider settings such as `RUNWAY_OUTPUT_HOSTS`, and the video smoke request.
  - It makes no network request and never prints a key. It exits 0 when ready.
- **Smoke tests (`python -m app.smoke_test`):**
  - `text` sends one request with at most 256 tokens and prints the latency, token usage, finish reason and a 160-character preview.
  - `video` submits the cheapest request the adapter accepts (shortest duration, lowest resolution, no audio), polls until the job ends or times out (900 s), downloads and validates the MP4 into `instance/smoke-tests/`, and prints the job ID, duration, size and elapsed time.
  - Both refuse to run without `--live` or `REELFORGE_LIVE_TESTS=1` in the shell (exit 3). The runtime file cannot set that flag.
  - `run-report <run_id>` compares each queued job with the settings in the run snapshot (model, language, tone, platform, length, style, duration, aspect ratio, prompt override) without calling a provider.
  - `tests/test_live_providers.py` wraps the same functions and is skipped unless `REELFORGE_LIVE_TESTS=1` (video also needs `REELFORGE_LIVE_VIDEO=1`).
- **Structured logging (`app/logs.py`):**
  - JSON lines on stderr, or `key=value` with `REELFORGE_LOG_FORMAT=text`.
  - Events:
    - executor: `workflow_run_started`, `workflow_run_rejected`, `workflow_run_status_changed`, `workflow_step_started`, `workflow_step_{completed,failed,blocked,queued,awaiting_review,needs_attention}`, `credit_reserved`;
    - text and video workers: `job_claimed`, `provider_request_{started,completed,failed}`, `job_retry_scheduled`, `job_completed`, `job_failed`, `credit_refunded`.
  - Fields: workspace, workflow, run, step and job IDs, provider and model, latency, token usage, and the error code, category, `retryable` flag and HTTP status.
  - A queued step logs the safe job fields next to the node's settings, so the snapshot settings and the request can be compared in the log.
  - Keys are scrubbed by value and by field name. Prompts, instructions and topics appear only as character counts.
  - The executor writes a pass's events when the pass ends, so a start rejected and rolled back (for example with HTTP 402) logs only `workflow_run_rejected`.
- **Error categories (`app/providers/errors.py`):**
  - Every text and video adapter error derives from one `ProviderError` with a `category`: `authentication_error`, `rate_limited`, `invalid_request`, `content_rejected`, `provider_unavailable`, `timeout`, `network_error`, `empty_output`, `invalid_response`, plus `billing_error`, `generation_failed` and `configuration_error`.
  - The adapters share the HTTP status mapping. Codes were renamed to match: `auth_error` → `authentication_error`, `transport_error` → `network_error` or `timeout`, `provider_response` → `invalid_response`, `content_blocked` → `content_rejected`, and "no text" → `empty_output`.
  - Codes kept for detail:
    - `submission_unknown`: a submit whose outcome is unknown, never refunded. Its category says whether it was a timeout, a network error or a 5xx.
    - `not_found`, `unsafe_url`, `billing_error`.
  - The refund rules are unchanged, except that HTTP 413, and 402 from Dola, now count as definite rejections.
  - Text step outputs store `error.category`.
  - `provider_detail` keeps the provider's own error message, at most 200 characters, from the error response. It appears in server logs (scrubbed of secret values) and in the smoke-test output. It never appears in a step output or an API response, which keep the plain message such as "openai returned HTTP 404".
- **Timeouts:** every provider call already had an explicit timeout. They are now tested and documented (docs/LIVE_PROVIDER_SMOKE_TEST.md#timeouts). A timeout on a status poll is `timeout` and retryable, where it was reported as a transport error before.
- **Refactors without behavior change:** the MP4 download, validation and new duration read (`mvhd`) moved to `app/video_files.py`, so the smoke test does not need the database.
- **Docs:** `docs/LIVE_PROVIDER_SMOKE_TEST.md` (setup, pre-flight, smoke tests, the full workflow test with expected node states, costs, troubleshooting, timeouts), the README (process start-up sequence), and the deployment guide (§4.1 runtime file).

### Phase 3.7 changes

Phase 3.7 adds explicit system-admin resolution of uncertain paid steps. Existing ledger and usage references remain unchanged; no new AI capabilities, pricing or provider integrations are added.

- **Accounting/service:** `app/reconciliation.py` validates workspace/run/step/job/reservation relationships, locks the run then account, and atomically appends usage or an exact refund plus one final decision. Same-decision requests return the original record; conflicting decisions return 409. Finalized usage cannot be refunded and refunded reservations cannot be charged. Resolution never calls the provider or debits a second time.
- **Schema:** migration `0011_credit_reconciliation` adds only `credit_reconciliations`. A step primary key, unique job/reservation references and decision/amount checks enforce one outcome. It retains actor, time, amount and optional note; application APIs cannot edit/delete history. Existing records are preserved; legacy attention steps need no backfill.
- **API:** `GET /api/admin/reconciliation` (`status=pending|resolved`, `limit=1..100`, `offset`), `POST /api/admin/reconciliation/{step_id}/confirm-charge`, and `POST /api/admin/reconciliation/{step_id}/refund`. Posts accept `{note?: string}` (max 1,000 characters), require system-admin authorization and same-origin validation.
- **Evidence:** `app/provider_progress.py` and the workers persist safe stage, remote ID, submission timestamps, last poll/state and structured error categories in step output. The admin API uses an explicit projection; credentials, raw payloads/responses and URLs remain private. Missing legacy facts are unknown.
- **Storage:** before the first video submit, recorded workspace bytes at/above quota cause failure/refund without provider submission. Final quota and MP4 validation remain. This does not predict generated size or reserve disk space.
- **Provider policy:** definitive pre-acceptance rejection refunds remain. All current video adapters require manual evidence for post-acceptance failure/cancellation; no generic `failed` state proves free work. See the provider table in `CREDIT_RECONCILIATION.md`.
- **Text:** deterministic rejection refunds and rate-limit retries remain. Timeouts, network/5xx/invalid responses, empty/content-filtered successful responses, and a worker reclaimed mid-call now require reconciliation rather than retry/refund. Happy-path text usage is unchanged.
- **Run/retry:** refund changes the step to `failed` and recomputes aggregate run state. Retry creates a fresh run/job/reservation only when there is no unresolved/confirmed-charge step, generated asset or approved review. Confirm charge leaves the historical step in `needs_attention` with a separate resolved marker; no success or asset is invented, and retry is blocked.
- **UI:** a Reconciliation tab in the existing Admin page provides pending/history lists, safe details and confirmed actions with optional notes. Regular users see operator-review/confirmed/refunded messages in vi/en/ja, without accounting controls.
- **Audit/logging:** immutable application decision rows plus post-commit `reconciliation_charge_confirmed` / `reconciliation_refunded` events with admin/workspace/run/step/job/credits/provider identifiers.
- **Tests:** `tests/test_reconciliation.py` covers API/auth, exact amounts, duplicate/opposite decisions including concurrency, prior usage/refunds, generic text reservations, legacy metadata, retry/asset guards and preservation of all existing tables across 0010→0011. `tests/test_reconciliation_workers.py` covers preflight, provider facts, uncertainty, deterministic refunds, sibling aggregation and stale workers. Existing text tests reflect the deliberate uncertainty-policy changes.
- **Validation (2026-09-30):** `python -m unittest discover -s tests -v` passed: 274 tests, 5 skipped (two opt-in live tests, one PostgreSQL test without a disposable test URL, two Windows symlink tests). `npm run typecheck` and `npm run build` passed in `frontend/`; `git diff --check` passed. Independent review findings on billed empty text, malformed legacy metadata and run/account lock ordering were fixed and verified. No live paid provider requests or live database migrations were performed. PostgreSQL concurrency still needs validation on a disposable PostgreSQL instance; SQLite tests cover concurrent admin decisions and emitted worker lock order.

### Phase 4 and 5 changes

Phase 4 makes the Image node executable. Phase 5 makes the Video node produce one clip per scene. Both share one child-job engine. No completed phase was redesigned: text jobs, single clips, the reconciliation procedure and the UI keep their behavior. TTS, subtitles, FFmpeg render and publishing executors are not part of this work.

- **Image layer (Phase 4):**
  - `app/providers/image/` defines `ImageGenerationProvider` and the Runway `gen4_image` adapter (`POST /v1/text_to_image`, task polling), chosen because it reuses the live-verified Runway client and its output host allowlist.
  - `app/image_files.py` downloads without redirects, caps files at 20 MB and accepts only PNG, JPEG and WEBP by signature; SVG is rejected.
  - AI tool task `image`; the price is `IMAGE_CREDITS_PER_GENERATION` (default 2).
- **Image node:** `app/workflow/nodes/image.py`.
  - Settings: model, aspect ratio (capability-checked), number of images 1–4, quality (720p or 1080p), prompt override, and an advanced seed.
  - Input priority: override > connected text > scenes (one image per scene, `visual_prompt` else `text`, never joined, `scene_index` kept) > project topic.
- **Multi-scene video (Phase 5):** `app/workflow/nodes/video.py`.
  - Mode precedence: override → one clip; scenes with an empty override → one clip per scene; only text → one clip.
  - The joined `Shot 1: … Shot 2: …` prompt and the one-video-node limit (`unsupported_graph`) are gone.
  - Retry freezes only a single-clip request of the matching node.
- **Jobs and credits:**
  - One durable job per operation: `image:<step>:<op>`, `video:<step>:scene:<n>`, `video:<step>:single`.
  - Each job has its own references: `<kind>-reserve:<step>:<op>`, `<kind>:<step>:<op>`, `<kind>-refund:<step>:<op>`.
  - The balance is checked for the whole step, under a lock, before anything is reserved.
  - New operations never use `reserve:<run_id>`. Legacy video payloads without references keep their per-run references in the worker and in reconciliation.
- **Child-job engine:** `app/media_jobs.py`.
  - Handles submit, poll, download, validate and store per job, with each job's facts in `step.output["jobs"]`.
  - Definite rejections before acceptance are refunded per job. Uncertain outcomes hold credits per job.
  - Successful files are always kept. The step settles only when all jobs end: `completed` if all succeeded, `needs_attention` if any is uncertain, else `failed`. Nothing is regenerated automatically.
- **Reconciliation:**
  - Migration `0012_job_reconciliation` moves the `credit_reconciliations` primary key to `job_id`, keeping an index on `step_id`.
  - Pending items are per job and carry `scene_index` and `operation`.
  - New routes `POST /api/admin/reconciliation/jobs/{job_id}/confirm-charge|refund`. The step routes still decide one-job steps and return 409 for steps with several jobs.
  - A step leaves `needs_attention` once no job is waiting and none was confirmed as charged.
- **Engine:** `NodeExecutionResult` can carry several `JobRequest`s, each with an optional `logical_key`. The executor enqueues all of them and logs their references.
- **API:** run responses strip private job facts (`submission`, `provider_job`, `error_count`). Readiness steps include `credits` and use code `per_scene` for per-scene estimates. Approval accepts any completed step that produced media.
- **Frontend:**
  - Image and video nodes show a preview grid or clip count and `Generating x/y` progress.
  - The inspector lists every file with its scene label, a player or preview and a download link, plus a per-job status list.
  - Readiness text covers per-scene credits. The reconciliation admin decides by job and shows the scene.
  - The Models page has a Runway · Gen-4 Image preset. Strings are in vi, en and ja.
- **Tools and operations:**
  - `python -m app.provider_check --only image` and `python -m app.smoke_test image --live`.
  - `run-report` lists every job of a step with its scene and compares image settings.
  - New unit `reelforge-image-worker`, restarted by `deploy.sh`. Multi-scene video needs no new process.
- **Tests:**
  - `tests/test_image_provider.py`, `tests/test_image_nodes.py`, `tests/test_image_worker.py`, `tests/test_multi_scene_video.py`. Together they cover:
    - mode precedence;
    - per-scene keys and references;
    - partial failure;
    - per-job reconciliation;
    - legacy `reserve:<run>` compatibility;
    - retry limits;
    - the mocked Idea → AI Writer → Scene Splitter (3) → Image (3) and Video (3) → Review workflow.
  - Existing tests were updated for per-step references and for scene mode replacing the joined prompt.
- **Validation (2026-10-01):**
  - `python -m unittest discover -s tests -v` passed: 313 tests, 5 skipped (two opt-in live tests, one PostgreSQL test without a disposable test URL, two Windows symlink tests).
  - `npm run typecheck`, `npm run build` and `git diff --check` passed.
  - `tests/test_job_reconciliation_migration.py` upgrades 0011 → 0012 → 0011 → 0012 on SQLite with existing decisions.
  - No live paid request and no live database migration was performed.
  - The PostgreSQL branch of migration 0012 (dropping the step primary key by its reflected name) is untested without a disposable PostgreSQL database.

### Phase 6, 7 and 8 changes

These phases turn the scene-aware workflow into one final video. No completed phase was redesigned: text, image and video jobs, credits and reconciliation, and the UI keep their behavior. Music, voice cloning, speech recognition, publishing executors and scheduling are not part of this work.

- **Voice (Phase 6):**
  - `app/providers/voice/` defines a provider-neutral `VoiceGenerationProvider`. The adapter is Google Gemini TTS (`gemini-2.5-flash-preview-tts` and `gemini-2.5-pro-preview-tts`, 30 prebuilt voices, delivery presets), chosen because `GEMINI_API_KEY` is already configured and live-verified.
  - The PCM response is wrapped in WAV. `app/audio_files.py` checks WAV and MP3 by their bytes and computes the WAV duration.
  - The Voice node (`app/workflow/nodes/voice.py`) reads, in order: the text override, then connected text (one narration), then scenes (one narration per scene from `scene.text`, never joined, `scene_index` kept). The project topic is never read.
  - Job kind `voice.generate`, keys `voice:<step>:single` and `voice:<step>:scene:<n>`, references `voice-reserve|voice|voice-refund:<step>:<op>`, price `VOICE_CREDITS_PER_GENERATION` (default 1).
  - `app/media_jobs.py` gained a synchronous path (`MediaKind.generate`): generate → validate → store. Ambiguous outcomes go to per-narration reconciliation (`voice.generate` is a paid kind).
- **Subtitles (Phase 7):**
  - `app/subtitles.py` times cues by narration, then clips, then scene estimates, then reading speed, and wraps them by characters and lines.
  - It escapes line breaks, `-->` and WebVTT markup, and writes SRT or WebVTT.
  - The Subtitle node writes the file as a private asset (provider `local`) during the executor pass. The file is deleted if the transaction rolls back.
  - Its `subtitle_asset` output carries the cues, the scene spans and the burn-in style. It is free, with no job and no worker.
  - Subtitle files do not block a retry after a refund.
- **Render (Phase 8):**
  - The Render node (`app/workflow/nodes/render.py`) checks, before queueing: the input assets and their files, FFmpeg and ffprobe, the subtitle font (`fc-list`), storage, and the optional price.
  - It freezes the clip order, narration and cues in a `render.generate` job (`render:<step>:final`).
  - `app/render_worker.py` probes the clips and re-times the subtitles onto the clips.
  - It runs one FFmpeg argument list (no shell, `cwd` = `<media>/.render-tmp/<job_id>`):
    - scale and pad to the first clip, 30 fps, concat;
    - narration per scene padded or cut to each clip, with the clip audio muted; else one narration for the whole video; else the clip audio;
    - `subtitles` burn-in with `force_style`;
    - H.264/AAC `+faststart`.
  - It checks the MP4 and stores it (provider `ffmpeg`, model `local`, `final: true` in `video_assets`).
  - Failures are final and refunded (never reconciled); FFmpeg messages are shown without paths. Rendering is free by default (`RENDER_CREDITS_PER_JOB=0`).
- **Review and publishing:**
  - Review after Render previews the final video only.
  - The API accepts MP4s from completed `render` steps for publishing.
  - The frontend's `approvedAsset` prefers a final render over clips.
- **Frontend:**
  - Voice shows a player (single) or a segment count and progress.
  - Subtitle shows its first cues, and the inspector adds format, cue count, timing source, download and a cue preview.
  - Render shows the final video and its length, and the inspector adds input counts, audio policy, resolution, download and the FFmpeg error.
  - The Review inspector previews the media awaiting approval.
  - The Models page has a Gemini TTS preset. Strings are in vi, en and ja; two image hints misplaced in Phase 4 were moved back to `config.hints`.
- **Tools and operations:**
  - `python -m app.provider_check --only voice`, `python -m app.smoke_test voice --live` and `python -m app.render_worker --check`.
  - `run-report` compares voice settings.
  - New systemd units `reelforge-voice-worker` and `reelforge-render-worker`, restarted by `deploy.sh`.
  - FFmpeg and Noto fonts are installation prerequisites.
- **Schema:** no migration. Assets, jobs, ledger and usage tables already fit; the newest migration is still `0012_job_reconciliation`.
- **Tests:** `tests/test_voice_provider.py`, `tests/test_voice_nodes.py`, `tests/test_voice_worker.py`, `tests/test_subtitles.py`, `tests/test_render.py`, `tests/test_render_worker.py`. The render tests include a real FFmpeg render of synthetic media, skipped where FFmpeg is missing.

### Phase 9 changes

Phase 9 makes the pipeline usable as one product. It adds no new provider or worker, and existing workflows and runs keep working.

- **Templates:**
  - `app/workflow/templates.py` builds **YouTube Short** (`youtube_short`: 9:16, 50 s script, 5 s scenes, 6 s clips) and **YouTube landscape** (`youtube_landscape`: 16:9, 120 s, 7 s scenes, 8 s clips). Each is Idea → AI Writer → Scene Splitter → Video + Voice + Subtitle → Render → Review → Publish, with Metadata.
  - They are created by `POST /api/workflows` with `template` and listed by `GET /api/workflow-templates`.
  - Templates set no `tool_id`; readiness now returns each step's resolved `tool` (the first enabled model for its task, or the step's own choice).
- **Metadata step:** `metadata`, a `TextNodeHandler` with `response_format = "json"`. It uses the same text worker, credits and reconciliation, and outputs `{title, description, tags}` (new port type `publish_metadata`), fitted to YouTube's limits and never failing on a non-JSON reply.
- **Publish step:**
  - The placeholder became a hand-off (`app/workflow/nodes/publish.py`). After Review, it completes with the final video and prepared metadata, and never uploads.
  - Metadata comes, in order, from its own settings, then the Metadata step, then connected text, then the project title.
  - Runs with Publish now end *completed* instead of *blocked*.
- **Run summary:** `GET /api/workflow-runs/{id}/summary` (`app/run_summary.py`), derived from steps, jobs, the ledger and usage events. It returns:
  - current and failed steps, needs-attention and blocked steps, and the render error;
  - active jobs;
  - counts of script words, scenes, images, clips, narrations and subtitle cues;
  - the final video;
  - credits reserved, used, refunded and held;
  - elapsed time;
  - publishing readiness and defaults.
- **Publishing:**
  - `app/publications.py` validates metadata like YouTube (title ≤ 100 characters, description ≤ 5,000 bytes, tags ≤ 500 characters, no `<` or `>`) before queueing.
  - It picks the final render (`final_video`) and refuses scene clips when a render exists.
  - It stores `privacy_status` and `tags`; the uploader sends them and accepts a more private result than requested, which YouTube applies to unverified projects.
  - Retry can correct metadata and queues only an upload job.
  - The API exposes tags, visibility, YouTube's upload status and applied visibility, and a watch URL.
- **Schema:** migration `0013_publication_metadata` adds `privacy_status` (default `private`), `tags` (default `[]`), `remote_status` and `remote_privacy`, with a visibility check constraint. It is tested for upgrade and downgrade with existing rows.
- **Frontend:**
  - Templates on the Workflows page.
  - The Run dialog shows the project topic and models.
  - A Summary panel in the run bar.
  - **Download final MP4** and **Prepare publishing**.
  - The publish form has tags and visibility, with live limit checks.
  - The Publishing page shows visibility, tags, processing, applied visibility, a watch link and retry with corrections.
  - The Metadata and Publish nodes show their results.
- **Tests:**
  - `tests/test_social_workflow.py`:
    - templates, defaults and model resolution;
    - the Short template run end to end with fake providers, through the summary, the hand-off, publication validation and idempotency, and a mocked YouTube upload;
    - retry without regeneration;
    - a legacy workflow.
  - `tests/test_publication_metadata_migration.py`.
  - Existing tests were updated for the Publish hand-off, the new node type and final-render-only publishing.

### Phase 10, 11, 12 and 13 changes

The workflow engine, durable jobs, credits, typed ports and publishing records are reused as they are. Existing workflows, runs and publications keep working: the YouTube routes, the Phase 9 hand-off shape and the Metadata output are unchanged, with new fields added beside them.

- **Phase 10 — content sources:**
  - New port type `source`; source steps `source_text`, `source_url`, `source_media` and `transcribe`.
  - New config field type `asset`, checked on save against the workspace's own uploads (`check_assets`).
  - Uploads accept TXT, MD, SRT and VTT (UTF-8, at most 2 MB; subtitles need cue timings).
  - `app/sources.py` fetches pages: HTTPS only, every resolved address public, the connection pinned to the checked IP with SNI, redirects re-checked (at most 3), 2 MB and 15 s limits, no JavaScript. It also extracts HTML text and parses SRT/VTT into timed segments.
  - The transcription provider layer (`app/providers/transcription/`, task `transcription`, OpenAI `whisper-1`, `verbose_json` segments) runs in the source worker. FFmpeg extracts audio in pieces of at most 20 minutes, and segment times are offset.
  - Transcription is reconcilable: `transcription.generate` was added to `PAID_KINDS`, with references `transcription-*:<step>:single`.
  - Template `repurpose`.
- **Phase 11 — Movie Recap:**
  - Port types `story` and `source_clips`.
  - `story_analysis` and `recap_script` are JSON text steps on the text worker.
  - `match_scenes` is local scene matching with a position fallback.
  - `extract_clips` queues `clips.extract` in the render worker: stream copy, then an H.264/AAC fallback. Each clip is stored as an asset with `source_asset_id`.
  - Render accepts the clips like generated clips.
  - Template `movie_recap`, with the rights notice "Use only content you are authorized to use."
- **Phase 12 — TikTok and Facebook:**
  - `app/publishers/channel_oauth.py`: OAuth with a single-use hashed state, encrypted tokens, TikTok refresh with rotation, Facebook long-lived token and Page selection, and channel statuses.
  - `app/social_worker.py`: TikTok inbox drafts (init, chunks, status) and Facebook Reels (start, transfer, finish, status). Progress is saved encrypted between steps. Uncertain outcomes become `needs_attention`, never a fake success.
  - Per-channel metadata validation (`validate_channel_metadata`), `platforms` in the Metadata and Publish outputs, and an owner-typed TikTok caption or Facebook description that is never replaced.
  - `POST /api/publications` for several channels at once.
  - TikTok and Facebook templates `tiktok_short` and `facebook_reel`.
- **Phase 13 — scheduling and hardening:**
  - Publication states `scheduled` and `cancelled`, with `scheduled_for` and `published_at` (UTC).
  - `dispatch_due` and the scheduler worker; reschedule and cancel until the upload starts.
  - Default models per task (`ExecutionContext.find_tool`: explicit choice, then the workspace default, then the first compatible model).
  - Worker heartbeats.
  - `GET /api/admin/jobs` (safe fields, counts, `stuck_jobs` audit) and `job_lease_reclaimed` logs.
  - `GET /api/storage` and `GET /api/admin/storage`.
  - Media cleanup of worker temp folders and orphans.
- **Schema:** migration `0014_channels_scheduling_ops` adds:
  - `channel_connections` and `channel_oauth_states`;
  - `worker_heartbeats`;
  - the two publication columns and states, and the index `ix_publications_due`;
  - `assets.source_asset_id`.

  Downgrading turns `scheduled` and `cancelled` publications into `failed`. It is tested on SQLite with existing rows; a PostgreSQL run is available with `REELFORGE_TEST_DATABASE_URL`.
- **Frontend:**
  - The new steps in the library and on the canvas, with an asset picker in the inspector.
  - The Channels page for three platforms, the OAuth callback page and Facebook Page selection.
  - A multi-platform publish dialog with per-channel tabs and scheduling.
  - The Publishing page: every channel, cancel, reschedule and retry.
  - The Calendar by scheduled time.
  - Default models and storage in Settings; **Admin → Operations**.
  - The Transcription task on AI Models.
  - vi/en/ja.
- **Tests (offline):**
  - `test_sources.py`: SSRF, redirects, limits, extraction, SRT/VTT.
  - `test_transcription_and_matching.py`: provider contract and errors, scene matching and its speed, recap parsing, clip commands.
  - `test_source_workflow.py`: uploads, sources, the URL worker, paid transcription with refund, reconciliation and crash handling.
  - `test_movie_recap.py`: the pipeline end to end, copy and re-encode fallback, lineage, render.
  - `test_multi_platform_publishing.py`: OAuth, statuses, three channels, both upload flows, failures and retries.
  - `test_platform_metadata.py`.
  - `test_scheduling.py`.
  - `test_operations.py`.
  - `test_channels_migration.py`.
- **Not verified live:** TikTok and Facebook app review and real uploads, OpenAI transcription, and FFmpeg clip cutting on real media. See the final report.

### Phase 14, 15 and 16 changes

The workflow engine, jobs, credits ledger, subscriptions and publishing are reused as they are; old workflows, orders and publications keep working.

- **Phase 14 — payments** ([PAYMENTS.md](PAYMENTS.md)):
  - `app/payment_providers/`: `PayOSProvider` (VietQR, the existing `app/billing.py`) and `OnePayProvider` (`onepay.py`: checkout URL, HMAC-SHA256 signature check, QueryDR); `METHODS = {"vietqr": "payos", "card": "onepay"}`; readiness without credentials.
  - `payments.apply_paid(..., provider=...)` refuses evidence from another provider; `payments.settle` applies paid evidence once (exact amount, row lock) and closes pending orders on failed/cancelled/expired evidence.
  - `POST /api/billing/checkout` takes `method`; `GET /api/billing` returns `methods` (configured only) and `orders_total`; `GET /api/billing/orders` pages the history.
  - OnePAY: `GET /api/billing/onepay/return` (a signed paid return is confirmed by QueryDR before settling; never by the browser alone) and `GET|POST /api/webhooks/onepay` (signed IPN). Card orders reuse `payment_orders`.
- **Phase 15 — admin console** ([OPERATIONS.md](OPERATIONS.md#admin-console-phase-15)):
  - `GET /api/admin` returns `COUNT`-based summary counts, plans and payment-provider readiness only.
  - `GET /api/admin/users|workspaces|payments` (+ `/{id}` details) page and search on the server (`q` escaped for `LIKE`, filters, `limit` ≤ 100); `POST /api/admin/payments/{id}/refresh`; `GET /api/admin/payment-providers`; jobs return `total`.
  - The Admin page fits the viewport (tabs Users, Studios & credits, Plans, Payments, Credit reconciliation, Operations); `DataTable` (`frontend/src/components/reelforge/data-table.tsx`) gives a sticky header, an internally scrolling body and pagination. Account creation moved into a dialog.
- **Phase 16 — product completion** ([FINAL_PRODUCT_AUDIT.md](FINAL_PRODUCT_AUDIT.md)):
  - `music` step (`app/workflow/nodes/music.py`, port type `music_track`) and Render mixing (`amix` under the narration, looped, configurable volume).
  - Render accepts still images (`-loop 1`, timed by narration or `RENDER_STILL_SECONDS`); `source_media` outputs images.
  - Templates `movie_review`, `article_to_video` and `product_video` (`_slideshow` graph).
  - Workspace defaults `default_platform`, `default_tone`, `default_duration` (applied by text steps that leave them empty) and `default_publish_time`; `PUT /api/settings/profile` (display name).
  - `GET /api/scripts`, `PATCH /api/assets/{id}` (attach an upload to a project), paginated `GET /api/publications` (`total`, calendar `start`/`end`).
  - Library entries are executable steps or presets of them; advanced items, Instagram, the `/ai/*` tool pages, the assistant panel and every Soon control were removed. `SoonBadge` and `ComingSoonBanner` were deleted.
- **Schema:** migration `0015_admin_payments_profiles` adds `user_profiles` and indexes `ix_workspaces_owner_id`, `ix_payment_orders_created_at`, `ix_payment_orders_provider_status`. Tested from 0014 and back on SQLite (`test_admin_payments_migration.py`; PostgreSQL with `REELFORGE_TEST_DATABASE_URL`).
- **Tests (offline):** `test_payments.py`, `test_admin_console.py`, `test_product_features.py`, `test_product_audit.py`, `test_admin_payments_migration.py`; existing tests updated where behaviour intentionally changed (music no longer a placeholder, image scenes render, new templates).
- **Not verified live:** OnePAY (sandbox and real), payOS after the refactor, a real FFmpeg render with music and stills, and the new templates with paid providers.

### Phase 17 changes

- **Storage module** (`app/storage.py`): the media root (`REELFORGE_STORAGE_ROOT`, else `storage_dir`), ID-only paths (`file_in`, `asset_path`, which refuse separators and dots), per-plan quotas capped by `WORKSPACE_MEDIA_QUOTA_BYTES`, warning levels (70/80/90/100 %), `lock_workspace` before every store, kinds, the retention policy and `expirable_query`. `app/media_paths.py` re-exports it; every upload, worker and node that stores media now uses it (the render and clip workers and the image/voice store also take the studio lock now).
- **Kinds** set at creation: `source`, `generated_image`, `scene_video`, `voice`, `subtitle`, `extracted_clip`, `final_render`; migration 0016 labels older assets from their step's node type.
- **Expiry keeps the row** (`bytes` 0, `expired_at`, `expired_reason`, `expired_bytes`): usage sums, the dashboard's `bytes > 0` filter and publishing's existing `bytes > 0` check need no schema-dependent query; downloads answer 410 `media_expired`.
- **Cleanup** (`app/media_maintenance.py`): `--intermediates` expires eligible intermediates (older than 30 days, run has a live final render, no publication) under row locks after a path-safety check, removes their files, and sweeps expired rows whose file survived; policy ages for partials (1 day), temp folders and orphans (3 days); `--dry-run` remains the default. A systemd timer at 03:00 is documented.
- **API**: `GET /api/storage` (level, intermediate summary, retention), `DELETE /api/assets/{id}`, `POST /api/assets/delete`, `POST /api/storage/cleanup` (owner; preview or apply), `GET /api/admin/storage` (paginated, levels, disk), storage per studio in `GET /api/admin/workspaces`, `counts.storage_alerts`, plan `storage_limit_bytes`.
- **Frontend**: storage meter and cleanup in Settings → Storage, a banner from 80 %, multi-select delete on Media, storage limits in Admin → Plans, a storage column in Studios, disk and levels in Operations, plan storage on Billing, a "file no longer available" placeholder.
- **Also**: Background Music `mode` (loop or play once); a blank numeric setting in the runtime file (as systemd passes `KEY=`) now means its default instead of failing.
- **Schema**: migration `0016_storage_lifecycle` (`plans.storage_limit_bytes`; `assets.kind`, `expired_at`, `expired_reason`, `expired_bytes`; `ix_assets_kind_created_at`), tested from 0015 and back.
- **Tests**: `test_storage_lifecycle.py`, `test_storage_migration.py`; `test_reconciliation_workers.py` now sets the quota through `WORKSPACE_MEDIA_QUOTA_BYTES` instead of patching the removed `video_worker.workspace_media_quota`.

### Phase 18 changes

- **Payment setup (18A)** (`app/payment_providers/setup.py`):
  - `GET /api/admin/payment-config` and `POST /api/admin/payment-config/check`, system admins only.
  - Shows field names with configured/missing/invalid. Only the payOS client ID and the OnePAY merchant ID are shown, masked.
  - Also shows OnePAY's mode from the payment URL, the callback URLs, and the last webhook/IPN/query/check times (system setting `payment_activity`).
  - The check is local. With `remote`, OnePAY receives one QueryDR about `RFCHECK<time>`. payOS remote is "unsupported". Secrets stay in bootstrap/env.
- **Notifications (18B)** (`app/notifications.py`):
  - The `notify` helper writes in the caller's transaction, with `ON CONFLICT DO NOTHING` on `(user_id, dedupe_key)`.
  - Hooks:
    - run status changes in `executor.advance_run`;
    - publications (scheduled, succeeded, failed, needs attention, schedule failure);
    - payments (paid, failed, paid_unapplied → admins);
    - `usage.post_credit` (admin adjustment, low balance on crossing);
    - storage 80/90/100 % in `storage.has_room` and uploads;
    - support.
  - The list, count, read and read-all endpoints are limited to studios the user still belongs to.
  - `GET /api/notifications/stream` is Server-Sent Events: cookie auth, `Last-Event-ID` resume, `unread` events, a 15 s ping, a 300 s lifetime, `no-transform` and `X-Accel-Buffering: no`. Re-authorized every pass.
  - Frontend: `NotificationStream` (one EventSource per tab, backoff, 30 s polling fallback), the bell with a "99+" badge, the popover, `/notifications`, and texts localized from `type` + `params`.
- **Support (18C)** (`app/support.py`):
  - Tickets with category, status and priority; append-only messages; validated context IDs.
  - The creator or the studio owner sees a ticket; admins see all; admin authors are hidden from users.
  - Admin → Hỗ trợ is a server-side table with a ticket dialog.
  - Notifications both ways.
- **Verification (18D)** (`app/readiness.py`):
  - `GET /api/admin/readiness`: database and migration head, storage write probe, disk and last cleanup, FFmpeg, workers, AI key presence, publishing, payments mode, open streams, open tickets.
  - The 18-item checklist in `verification_checks`, ticked only by an admin.
  - Admin → Kiểm định, with a browser stream check.
  - `media_maintenance --apply --intermediates` records its last run.
- **Schema:** migration `0017_notify_support_verify`: `notifications`, `support_tickets`, `support_messages`, `verification_checks`. Tested from 0016 and back on SQLite and PostgreSQL 16. Revision IDs must fit `alembic_version.version_num` (32 characters), which a test now enforces.
- **Tests:**
  - `test_phase18.py`: notifications and SSE, support, admin payment setup, readiness and checklist; sentinel secrets never leak.
  - `test_phase18_migration.py`.
  - `test_product_audit.py` (Phase 18 frontend checks).
- **Not verified live:** the stream through Cloudflare on the real domain, and payOS/OnePAY activity from real callbacks.

### Phase 19 changes

- **Resolver** (`app/payment_config.py`): `get(provider)` returns the source (`admin`, `bootstrap`, `environment`, `missing`), the switch, the mode, the values and any error.
  - `payos_credentials`, `onepay_config` and `onepay_urls` build what the providers use; `usable` and `offered` decide availability.
  - `issues` explains unavailability; `save`, `set_enabled` and `audit` apply and record changes.
  - `app/billing.py`, `payment_providers` (`OnePayProvider.config()`), `setup.py` and `readiness.py` all go through it. `onepay.configured()` and `OnePayConfig.from_environment()` remain the legacy readers.
- **Encryption** (`app/secret_box.py`): Fernet with an HKDF-SHA256 key per purpose derived from `REELFORGE_TOKEN_ENCRYPTION_KEY`. It reports `key_missing` or `cannot_decrypt`; it never generates a key.
- **API:**
  - `GET /api/admin/payment-config` returns a new shape: `fields` keyed by name, `source`, `enabled`, `available`, `issues`, `history`, `urls`.
  - New routes: `PUT /api/admin/payment-config/{provider}` (keep/replace/clear per secret; `confirm_production`) and `POST …/{provider}/check|enable|disable`.
  - `GET /api/billing` offers only enabled, valid methods; checkout answers 503 for the others. `payment_providers.readiness()` adds `enabled`, `available` and `source`.
  - Request bodies are parsed manually, so a 422 never echoes a submitted value.
- **Frontend:**
  - `PaymentGatewaysDialog` (`admin/payment-gateways.tsx`) replaces the read-only setup dialog, with VietQR/Card tabs, write-only secret fields, mode badges, the production confirmation, issues, URLs, activity and history.
  - The Admin header warns when no gateway is enabled; Plans show purchasability.
  - Billing names the methods "VietQR / Bank Transfer" and "Credit / Debit Card" without provider internals.
  - The checklist is grouped.
- **Checklist:** `payos_payment` and `onepay_sandbox_payment` are kept; eleven payment items are added (29 in all, grouped).
- **Schema:** migration `0018_admin_payment_config` adds `payment_provider_configs` and `payment_config_audit`, tested from 0017 and back.
- **Tests:**
  - `test_phase19.py`: security, payOS and OnePAY programs;
  - `test_phase19_migration.py`;
  - `Phase19FrontendTest` in `test_product_audit.py`;
  - `test_phase18.py` updated for the new response shape.
- **Not verified live:** saving real credentials, the OnePAY sandbox endpoints, and real payments.

### Configuration sources

- **`instance/bootstrap.json`:** `database_url` and optional legacy `payos` credentials.
- **`payment_provider_configs` table:** admin-managed payment gateway configuration, encrypted (Phase 19).
- **`system_settings` table:** `frontend_origin`, `secure_cookies`, `storage_dir`, `trial_project_limit`, `registration_enabled`.
- **`plans` table:** `storage_limit_bytes` per plan (Phase 17).
- **`workspace_settings` table:** `default_language`, `video_orientation`, `approval_required`, `default_platform`, `default_tone`, `default_duration`, `default_publish_time` (Phase 16).
- **`user_profiles` table:** optional `display_name` (Phase 16).
- **Environment variables (API and workers):**
  - Video provider keys: `FAL_KEY`, `RUNWARE_API_KEY`, `REPLICATE_API_TOKEN`, `RUNWAYML_API_SECRET`, `DOLA_API_KEY`.
  - Text provider keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`.
  - Provider settings: `DOLA_EXPERIMENTAL_ENABLED`, `DOLA_BASE_URL`, `DOLA_MEDIA_BASE_URL`, `DOLA_MAX_JOB_AGE_SECONDS`, `RUNWAY_OUTPUT_HOSTS`.
  - Limits and prices: `VIDEO_CREDITS_PER_CLIP`, `TEXT_CREDITS_PER_GENERATION`, `IMAGE_CREDITS_PER_GENERATION`, `VIDEO_JOB_MAX_AGE_SECONDS`, `IMAGE_JOB_MAX_AGE_SECONDS`, `VOICE_CREDITS_PER_GENERATION`, `VOICE_JOB_MAX_AGE_SECONDS`, `RENDER_CREDITS_PER_JOB`, `RENDER_TIMEOUT_SECONDS`, `WORKSPACE_MEDIA_QUOTA_BYTES`.
  - Rendering: `RENDER_FFMPEG_PATH`, `RENDER_FFPROBE_PATH`, `RENDER_SUBTITLE_FONT`, `RENDER_STILL_SECONDS`.
  - Storage (Phase 17): `REELFORGE_STORAGE_ROOT`, `WORKSPACE_MEDIA_QUOTA_BYTES` (a cap), `REELFORGE_RETENTION_INTERMEDIATE_DAYS`, `REELFORGE_RETENTION_TEMP_DAYS`, `REELFORGE_RETENTION_PARTIAL_DAYS`, `REELFORGE_RETENTION_ORPHAN_DAYS`.
  - Notifications (Phase 18): `REELFORGE_SSE_POLL_SECONDS`, `REELFORGE_SSE_MAX_SECONDS`, `CREDITS_LOW_THRESHOLD`.
  - Card payments (Phase 14; legacy fallback since Phase 19): `ONEPAY_MERCHANT_ID`, `ONEPAY_ACCESS_CODE`, `ONEPAY_HASH_KEY`, `ONEPAY_PAYMENT_URL`, `ONEPAY_QUERY_URL`, `ONEPAY_QUERY_USER`, `ONEPAY_QUERY_PASSWORD`.
  - YouTube OAuth: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, `REELFORGE_TOKEN_ENCRYPTION_KEY`.
  - Runtime file and logs: `REELFORGE_ENV_FILE` (default `.env.runtime`), `REELFORGE_LOG_FORMAT`, `REELFORGE_LOG_LEVEL`.
  - Live smoke tests: `REELFORGE_SMOKE_TEXT_PROVIDER`, `REELFORGE_SMOKE_TEXT_MODEL`, `REELFORGE_SMOKE_VIDEO_PROVIDER`, `REELFORGE_SMOKE_VIDEO_MODEL`, `REELFORGE_SMOKE_IMAGE_PROVIDER`, `REELFORGE_SMOKE_IMAGE_MODEL`, `REELFORGE_SMOKE_VOICE_PROVIDER`, `REELFORGE_SMOKE_VOICE_MODEL`; `REELFORGE_LIVE_TESTS=1` and `REELFORGE_LIVE_VIDEO=1` only from the shell.
  - Tests only: `REELFORGE_TEST_DATABASE_URL`.
- **`.env.runtime`** (git-ignored; template `.env.runtime.example`): the environment variables above, loaded by every process (Phase 3.6).
- **`frontend/instance/config.json`:** `api_base_url`.
- **Deployment:** systemd units for the API, frontend, video worker, text worker, image worker, voice worker, render worker and YouTube worker (`docs/home-server-deployment.md`, `deploy.sh`). System FFmpeg and Noto fonts for rendering.

### Where each audited area is covered

| # | Area | Status | Items |
| --- | --- | --- | --- |
| 1 | Workflow graph persistence | Fully implemented, including per-node `config` (edited in the inspector since Phase 3.5) and edge ports | F1, F13 |
| 2 | Workflow execution engine | Fully implemented (modular executor and registry); some run semantics still partial | F10, P1 |
| 2b | Typed data passing between nodes | Fully implemented (ports, legacy fallback, required inputs) | F12 |
| 3 | Node types | Partial (20 registered; 18 do real work) | P2, U2, U3 |
| 4 | Job architecture | Fully implemented (core) | F3 |
| 5 | Video generation providers | Partial | P3 |
| 5b | Text generation providers | Fully implemented; Gemini live-verified by the operator, other providers mock-tested | F11 |
| 6 | Asset/media persistence | Partial; pre-submit full-quota check added in Phase 3.7 | P4 |
| 7 | AI tool/provider configuration | Partial | P5 |
| 8 | Credits reservation and usage tracking | Hold, usage, refund and manual reconciliation implemented; pricing/grants remain open | P6, F11, M6, R1 |
| 9 | Publishing architecture | Partial | P7, M3 |
| 10 | YouTube integration | Fully implemented (private by default; unlisted/public and tags since Phase 9) | F4 |
| 11 | Frontend API integration | Fully implemented | F7 |
| 12 | Workflow node status rendering | Fully implemented (polling) | F8 |
| 13 | Tests | Partial | P8 |
| 14 | Migrations | Fully implemented | F9 |
| 15 | Error handling | Partial | P9 |
| 16 | Retry behavior | Partial | P10 |
| 17 | Security boundaries | Partial | P11, R2–R4 |
| 18 | Workspace isolation | Fully implemented (single-member model) | F2, M8 |

## Live Provider Verification

The operator confirmed successful live Gemini text generation, Runway `gen4.5` video generation and `Idea → AI Writer → Scene Splitter → Video → Review` before the Phase 3.7 handoff. This supersedes the earlier no-credentials audit snapshot. Exact run IDs, timings, token counts, Gemini model and execution dates were not provided; they are not inferred here. Phase 3.7 does not repeat those paid calls.

| Provider | Modality | Model | Mock tested | Live verified | Date | Status |
| --- | --- | --- | --- | --- | --- | --- |
| OpenAI | text | `gpt-4.1-mini` (smoke default) | yes | no | — | Mock tested only |
| Anthropic | text | `claude-haiku-4-5-20251001` (smoke default) | yes | no | — | Mock tested only |
| Gemini | text | Exact live model not supplied (`gemini-2.5-flash` is the smoke default) | yes | yes, operator-confirmed | Not supplied | Text generation passed |
| fal | video | `fal-ai/veo3.1/fast` | yes | no | — | Mock tested only |
| Runware | video | `bytedance:seedance@2.5` | yes | no | — | Mock tested only |
| Replicate | video | `google/veo-3.1-fast` | yes | no | — | Mock tested only |
| Runway Dev | video | `gen4.5` | yes | yes, operator-confirmed | Not supplied | Video generation and full workflow passed |
| Runway Dev | image | `gen4_image` | yes | yes, operator-confirmed | Not supplied | Scene mode and the Image + Video workflow passed, with credit accounting |
| Gemini | voice | `gemini-2.5-flash-preview-tts` | yes | no | — | Mock tested only (`python -m app.smoke_test voice --live` not run) |
| FFmpeg (local) | render | system `ffmpeg` | yes (fake runner) | no | — | Real render test skipped: FFmpeg not installed on the development machine |
| Dola (experimental) | video | `seedance-2.0`, `seedance-2.5` | yes | no | — | Mock tested only; out of scope |
| YouTube Data API | publishing | — | yes | no | — | Mock tested only (visibility and tags since Phase 9) |
| payOS | payment (VietQR) | — | yes | no | — | Mock tested only; checkout and webhook unchanged by the Phase 14 refactor |
| OnePAY | payment (card) | `vpc_Version` 2, QueryDR | yes | no | — | Mock tested only (signed fake gateway); sandbox and one real payment required |

"Mock tested" means the adapter's request shape, response parsing, error mapping and timeouts are covered offline with `httpx.MockTransport` or fake clients, and the workflow path is covered with fake providers. Additional providers still require live verification. Preserve the exact date/model/command and latency/tokens or job ID/duration/size when further operator evidence becomes available.

## Fully Implemented

### F1. Workflow graph persistence (topology, layout and node settings)

- **Files:**
  - `app/main.py`: `GraphNode`, `GraphEdge`, `WorkflowGraph`, `NODE_TYPES`, `validate_graph`, `workflow_graph`, `default_graph`, `create_workflow`, `update_workflow`.
  - `app/workflow/ports.py`: `normalize_edges`, `edge_problems`.
  - `app/models.py`: `Workflow.definition`.
  - Frontend: `frontend/src/components/workflow/workflow-editor.tsx` (`toNodes`, `toEdges`, `save`) and `frontend/src/lib/hooks.ts` (`useCreateFromTemplate`).
- **Current:**
  - **Storage:** the graph is JSON text in `workflows.definition`. Nodes are `{id, type, x, y, label, config}`, and edges are `{source, target, sourceHandle, targetHandle}`. The handles name ports (F12).
  - **Server validation:**
    - Structure: 1–30 nodes and at most 60 edges, unique node IDs, one of the 19 registered node types, and finite coordinates within ±100 000.
    - Edges: no dangling or self edges, no duplicate edge through the same ports, and no cycles (Kahn's algorithm).
    - Ports (on save only): named ports must exist and have compatible types, and an input that takes one connection gets only one.
  - **Node settings:** `config` is optional, at most 8,000 characters of JSON, and validated against the node type's `config_fields` (F13). Types without settings reject any config. The inspector edits it, and the canvas keeps it when it saves or duplicates a node.
  - **Legacy data:** list-form definitions are converted on read. Edges without handles get their default ports when read, saved or run (`workflow_graph`).
  - **Defaults:** a new workflow starts as `idea → video → review`.
  - **Editor:** add, drag, connect (with a client-side cycle check), rename (`label`), duplicate, delete, undo/redo and explicit save. Templates create a workflow and then `PUT` their graph.
  - **Permissions:** only the workspace owner can save; the workflow count is limited by the plan.
- **Missing:**
  - Rename and delete endpoints for workflows.
  - Conflict detection: the last save wins across tabs.
  - Version history for definitions. Runs do keep their own `graph_snapshot`.
  - A `GET /api/workflows/{id}` endpoint: the editor finds its workflow inside `/api/dashboard`.
- **Depends on:** nothing. P1 and F13 build on it.

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

### F4. YouTube integration (uploads with chosen visibility)

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
    - It uploads in 8 MiB chunks with `containsSyntheticMedia=true` and the chosen `privacyStatus` (private by default; unlisted or public since Phase 9) and tags. A more private answer than requested (unverified Google projects) is accepted and recorded; a more public one needs attention.
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
  - **Coverage:** every existing backend route is used: auth, dashboard, projects, workflows, the node-type port catalog (`useNodeTypes`, fetched once), runs (including readiness, approve and retry), asset upload and download, AI tools CRUD, settings, billing, usage, admin, and the YouTube connection and publications.
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
    - The video node previews the generated MP4, and text nodes preview the first lines of their text; the inspector shows all of it. The scenes node shows its scene count, with the full list in the inspector. The idea and assets nodes show their real step output.
    - Each node lists its typed input and output ports as labeled handles (F12).
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

- **Files:** `migrations/versions/0001_initial.py` … `0011_credit_reconciliation.py`, `migrations/env.py`, `alembic.ini`, `app/db.py`.
- **Current:**
  - A linear chain from 0001 to 0011, all with downgrades.
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
  - `app/workflow/context.py`: `ExecutionContext`, `RunOptions`, `NodeInputs`, `StepState`, `resolve_node_inputs` (Phase 3).
  - `app/workflow/results.py`: `NodeExecutionResult`, `NodeError`, `JobRequest`, `NodeReadiness`, `RunRequestError`, `derive_run_status`, and the status constants.
  - `app/workflow/graph.py`: `parse_graph`, `ordered_nodes`.
  - `app/workflow/nodes/`: `base.py` (`NodeHandler`), `idea.py`, `assets.py`, `video.py`, `review.py`, `pending.py`.
  - Callers: `app/main.py` (`persist_run`, `workflow_readiness`, `approve_workflow_run`), `app/video_worker.py` (`_store_result`), `app/text_worker.py` (`_finish`).
  - Tests: `tests/test_workflow_engine.py`, plus the existing run, video and YouTube flow tests.
- **Current:**
  - **Registry:** each node type resolves to exactly one `NodeHandler`, and registering a type twice raises. An unregistered type resolves to `UnsupportedNodeHandler`, which blocks the step with a clear reason instead of crashing the run.
    - `idea`, `assets`, `scenes` (Phase 3), `video`, `review` and the seven text nodes (F11) have real handlers.
    - `script`, `image`, `voice` and `music` use `PendingAITaskHandler`; `subtitle`, `render` and `publish` use `PendingServiceHandler`. Both keep the existing messages, and both declare their future ports.
    - The API accepts exactly the registered types (`NODE_TYPES`).
  - **Handler contract:** `execute(context, node, inputs)` returns a `NodeExecutionResult`, `readiness(context, node)` returns a `NodeReadiness`, and `validate_config(config)` checks saved settings.
    - `context` carries the database session, workspace, project, run, snapshot graph, the run's steps (`step_for(node)`), run options (prompt override, tool ID, frozen video payload) and cached lookups (enabled tools, `find_tool`, assets, workspace settings, credit balance).
    - Handlers never commit; everything happens in the caller's transaction.
  - **Standard result:**
    - Fields: `status`, `detail`, `output`, `metadata`, `error`, `job`, `job_id`, `asset_ids`.
    - Statuses reuse the stored values: generic *pending* is `skipped`, and *needs review* is `awaiting_review`. The model rejects unknown statuses, a `queued` result without a job, or a `failed` result without an error.
    - `output` is persisted together with `asset_ids` and `error.code`. `metadata` is not persisted. `job_id` is filled in after enqueue.
  - **Input resolution:** since Phase 3, `resolve_node_inputs` gives each node:
    - its `config`;
    - the status and output of each direct parent;
    - one value per input port, from edges, then config, then context (`inputs.get(port)`, F12).
    - `value(key)`, `outputs(node_type)` and `asset_ids` remain for code that needs raw parent outputs.
    - A node runs only when every parent has completed; otherwise it stays `skipped`. A node whose required inputs have no value blocks (`missing_input`) without its handler running.
  - **Execution passes:**
    - `start_run` first creates one step row per node, then evaluates the snapshot in topological order, enqueues `JobRequest`s (key `<kind>:<run>:<step>`), and derives the run status.
    - `finish_step` records a worker's result and calls `advance_run`.
    - `advance_run` locks the run row, then re-evaluates only `skipped` steps whose parents are now all completed. It never rewrites steps that already left the pending state.
    - The run status comes from `derive_run_status`: any active step → `running`; then `awaiting_review`, `needs_attention`, `failed`, all completed → `completed`; otherwise `blocked`.
  - **Errors:**
    - A handler exception fails only that step (`handler_error`, logged); its dependents stay `skipped`.
    - `RunRequestError` (for example 402, not enough credits) rolls back the whole run request while the run is starting, with the same HTTP status and message as before. Later, when a node becomes ready inside a worker or approval pass, it blocks that step with its `code` and `step_detail` instead.
    - The video worker's failure path (`_terminal_failure`) and retry rules are unchanged (P10). The text worker reports failures through `finish_step`.
  - **Compatibility:** no migration. Legacy list definitions, snapshots without labels, and runs already in flight from the previous engine all continue (tested).
- **Missing:** see P1 for run semantics that are still limited, and M1 for a settings UI.
- **Depends on:** F1, F3. P1, P2, M1, M2 and F11 build on it.

### F11. Text AI provider layer and text nodes (Phase 2)

- **Files:**
  - `app/providers/text/base.py`: `TextGenerationProvider`, `TextResult`, `TextUsage`, `TextProviderError`, request validation, HTTP error mapping.
  - `app/providers/text/openai.py` (Chat Completions), `anthropic.py` (Messages), `gemini.py` (generateContent).
  - `app/providers/text/__init__.py`: `TEXT_PROVIDERS`, `create_text_provider`, `text_provider_config_issue`, `text_credit_cost`.
  - `app/workflow/nodes/text.py`: `TextNodeHandler` and the `ai_writer`, `summarize`, `rewrite`, `translate`, `hook`, `title` and `cta` handlers.
  - `app/text_worker.py`.
  - Frontend: `frontend/src/lib/workflow.ts` (`TEXT_NODES`, library mapping), `frontend/src/components/workflow/studio-node.tsx` (`ai` preview), `frontend/src/components/workflow/workflow-editor.tsx` (inspector text), `frontend/src/app/models/page.tsx` (Text presets).
  - Tests: `tests/test_text_providers.py` (12), `tests/test_text_nodes.py` (14, including one over HTTP).
- **Current:**
  - **Interface:** `generate(model, prompt, system_prompt, temperature, max_tokens, response_format)` returns `TextResult(text, usage, provider, model, raw_metadata)`.
    - `usage` has input, output and total tokens; the total is computed when the vendor omits it.
    - `raw_metadata` holds only the response ID and the normalized and vendor finish reasons. Vendor response bodies never leave the adapter.
    - Requests are validated before any call: model name, prompt length, temperature 0–2, `max_tokens` 1–32 768, and `response_format` `text` or `json`.
    - Temperature is sent only when set, because reasoning models reject non-default values.
  - **Adapters:** direct HTTP with `httpx` (no vendor SDK), no redirects, and a 120 s read timeout.
    - OpenAI uses `max_completion_tokens` and `response_format: json_object`.
    - Anthropic uses `anthropic-version: 2023-06-01`, and JSON mode through the system prompt.
    - Gemini uses the `x-goog-api-key` header (never the URL), rejects model names that could change the URL path, sets `responseMimeType` for JSON, and skips thought parts.
  - **Errors:** stable codes.
    - Deterministic rejection/configuration errors include `invalid_request`, `authentication_error`, `billing_error`, `not_found`, `missing_key`, `unsupported_provider`.
    - The worker only automatically retries `rate_limited`; provider retryability flags do not prove a paid request is safe to send again. `provider_unavailable`, `timeout`, `network_error`, `invalid_response`, `content_rejected` and `empty_output` require reconciliation (Phase 3.7).
    - Messages never include the key or a response body.
  - **Selection and keys:**
    - Text nodes use the first enabled AI tool with task `script` (shown as "Text") and provider `openai`, `anthropic` or `gemini`, or the tool named by the node's `config.tool_id`. The model string is passed through, not allow-listed.
    - Keys come only from `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`. A missing key blocks the step, or shows `missing_key` in readiness.
  - **Nodes and inputs** (through typed ports since Phase 3, F12; text inputs are capped at 60,000 characters):
    - `ai_writer`:
      - Inputs `prompt` (brief: connected idea, else `config.prompt`, else project topic) and `source` (connected text).
      - Optional `language`, `tone`, `platform`, `duration` (converted to a word target at 2.5 words per second) and `instructions`.
      - Output `script`.
    - `summarize`, `rewrite` and `translate`: input `text` (connected text or brief, else the project topic).
      - `rewrite` uses `instructions` as the style.
      - `translate` uses `target_language`, else `language`, else the workspace default language.
      - Outputs `summary`, `text` and `text`.
    - `hook`, `title`, `cta`:
      - Inputs `topic` and `source`; `count` alternatives, one per line.
      - Outputs: the first alternative under the node's own name, all of them under `text`, plus `options`.
    - With nothing to work from, the step blocks (`missing_input`) and nothing is charged.
    - Every output also has `provider`, `model`, `usage` and `language`.
  - **Execution:** the handler holds credits and queues a `text:` job. `python -m app.text_worker` claims it, marks the step `running`, calls the provider outside any DB transaction, and then, in one transaction:
    - closes the job;
    - records a `UsageEvent`, or refunds the hold;
    - saves the output through `finish_step`, so downstream steps run.
    - Rate-limit rejections retry with backoff 5 s × 2ⁿ, at most 3 attempts. Other ambiguous errors and workers that died mid-call keep credits held for reconciliation.
  - **Credits:**
    - A flat `TEXT_CREDITS_PER_GENERATION` (default 1, range 1–100 000) is held when the step is queued (`text-reserve:<step>`).
    - On success, one `UsageEvent` (`text:<step>`, tool `<provider>/text`, units = total tokens) records the charge.
    - Deterministic terminal failures refund (`text-refund:<step>`). Uncertain outcomes require an audited charge/refund decision.
    - The ledger never goes negative. Not enough credits rejects a run at start (402 "Not enough credits for this step"), or blocks a later step with `insufficient_credits`.
  - **Frontend:**
    - The AI library items `aiWriter`, `summarize`, `rewrite`, `translate`, `generateHook`, `generateTitle` and `generateCta` add these nodes.
    - The canvas shows queued/running/completed/failed from the run steps (5 s polling), with a four-line text preview.
    - The inspector shows the full text.
- **Missing:**
  - Live verification for providers other than operator-confirmed Gemini; automated tests use mocked HTTP or a fake provider. The settings inspector is implemented in Phase 3.5 (M1).
  - Token-based pricing. The flat price ignores output length, so `max_tokens` (up to 8,192 per node) bounds provider cost per credit.
  - Streaming, prompt caching, per-workspace keys, per-provider rate limits, and moderation of generated text before it is used downstream.
  - The existing `script` node still uses its placeholder. It could reuse `TextNodeHandler`, but switching it would start charging existing workflows, so it was left for a decision.
  - Other job kinds still need their own worker process (text and video each have one).
- **Depends on:** F10, F3, P5 (AI tools), P6 (ledger).

### F12. Typed data passing between nodes (Phase 3)

- **Files:**
  - `app/workflow/ports.py`: `InputPort`, `OutputPort`, data types, `bind_edges`, `normalize_edges`, `edge_problems`, `describe_node_types`.
  - `app/workflow/context.py`: `resolve_node_inputs`, `NodeInputs.get/has/sources`.
  - `app/workflow/nodes/*.py`: the `inputs`, `outputs` and `requires` of each handler.
  - `app/workflow/nodes/scenes.py`: `split_scenes`, `ScenesNodeHandler`.
  - `app/main.py`: `GraphEdge.sourceHandle/targetHandle`, `validate_graph`, `workflow_graph`, `GET /api/workflow-node-types`.
  - Frontend: `frontend/src/components/workflow/ports.ts`, `studio-node.tsx` (`Ports`), `workflow-editor.tsx` (`isValidConnection`, `onConnect`, `toEdges`, `save`), `frontend/src/lib/queries.ts` (`useNodeTypes`).
  - Tests: `tests/test_workflow_ports.py` (19, including one over HTTP).
- **Current:**
  - **Data types:** `brief` (short idea: topic, title), `text` (written content), `scenes` (list of `{index, text, visual_prompt, duration}`), `video_assets`, `image_assets` and `audio_assets` (lists of `{id, filename?, content_type?}`), `subtitle_asset`, and `publication` (reserved). Values of the wrong shape are dropped rather than passed on.
  - **Ports per node** (outputs first = default):

    | Node | Inputs | Outputs |
    | --- | --- | --- |
    | `idea` | — | `topic`, `title` (brief) |
    | `assets` | — | `video_assets`, `image_assets`, `audio_assets` |
    | `ai_writer` | `prompt` (brief/text), `source` (text) | `script` |
    | `summarize` / `rewrite` / `translate` | `text` (text/brief) | `summary` / `text` / `text` |
    | `hook` / `title` / `cta` | `topic` (brief/text), `source` (text) | `hook` / `title` / `cta`, `text` |
    | `scenes` | `script` (text/brief) | `scenes` |
    | `video` | `prompt` (text/brief), `scenes` | `video_assets` |
    | `review` | `media` (video/image assets) | `video_assets` (the approved clip) |
    | `script`, `image`, `voice`, `music`, `subtitle`, `render`, `publish` | declared for the future executor | `publish` has no output yet |

  - **Edge mapping:**
    - An edge with `sourceHandle`/`targetHandle` connects those ports.
    - A legacy edge connects the source's default output to the first target input accepting that type, preferring inputs that list the type earlier. For example, idea → writer maps `topic → prompt`, and writer → hook maps `script → source`.
    - Explicit edges claim inputs before legacy edges. An input that takes one connection is not filled twice.
    - An edge that cannot carry data still orders the run.
  - **Resolution:**
    - Priority: connected edges, then the node's config (`config_key`), then run context (`project_topic`).
    - Several connections to a `multiple` input are joined: text with blank lines, lists concatenated, in edge order.
    - `NodeInputs.sources` records where each value came from.
  - **Required inputs:**
    - `requires` groups: for example `scenes` needs `script`, and summarize/rewrite/translate need `text`.
    - A missing input blocks the step with a node-specific message and `output.missing_inputs`.
    - The video node keeps its own check, because its prompt override setting and the project topic can supply its prompt.
    - Readiness runs the same check before a run: a required group that no edge, setting or context fallback can fill reports `missing_input` (Phase 3.5).
  - **Compatibility:**
    - No migration and no version field.
    - Old edges are normalized when read, saved or run.
    - A snapshot naming a port that no longer exists falls back to the default mapping.
    - Phase 2 outputs stored under `text` are still read (`OutputPort.keys`).
  - **Canvas:**
    - Handles are the ports, with labels.
    - Only compatible ports accept a connection while dragging.
    - A single-connection input swaps its old edge for the new one.
    - Nodes without inputs keep an inert handle, so old edges into them still draw.
    - Raw JSON only appears in advanced mode.
- **Missing:**
  - **Scene splitting is rule-based** (paragraphs, then sentences). An AI splitter with better visual prompts could replace it, using the text layer's JSON output.
  - **The video node makes one clip for all scenes;** multi-clip rendering is still open (M2).
  - **No explicit type conversions** (for example, extracting a list of titles from `text`), and no per-field mapping; by design, there is no expression language.
  - **Ports are described by code,** not a versioned schema; renaming a port relies on the tolerant fallback.
  - **The inspector cannot yet show resolved input values** before a run; it shows what each input port is connected to.
- **Depends on:** F10, F1.

### F13. Node configuration (Phase 3.5)

- **Files:**
  - `app/workflow/config.py`: `ConfigField`, `ConfigError`, `validate_config`, `config_values`, `check_tools`.
  - `app/workflow/nodes/base.py` (`config_fields`, `validate_config`, `config_values`), `text.py`, `scenes.py`, `video.py`.
  - `app/workflow/executor.py` (`_evaluate`, `_readiness`), `app/workflow/ports.py` (`unsatisfied_inputs`, `describe_node_types`).
  - `app/main.py`: `validate_graph`, `config_error`, `update_workflow` (tool check), `workflow_readiness`, `retry_workflow_run`.
  - Frontend: `frontend/src/components/workflow/node-config.ts`, `config-fields.tsx`, `workflow-editor.tsx` (`Inspector`, `setConfig`, `save`, `RunDialog`), `types.ts` (`readinessText`), `frontend/src/lib/api.ts` (`ApiError.error`), `frontend/src/lib/i18n/{vi,en,ja}.ts` (`config`).
  - Tests: `tests/test_node_config.py` (17, including one over HTTP).
- **Current:**
  - **One schema:** each handler declares `config_fields`. The same list validates saves, blocks invalid nodes at run time, fills defaults for the handler, and is served to the editor, which renders the inspector from it.
  - **Validation:** on save, 422 with `{code, field, node_id, message}`. Codes include `invalid_language`, `invalid_tone`, `invalid_platform`, `invalid_duration`, `invalid_length`, `invalid_count`, `invalid_style`, `invalid_scene_limit`, `invalid_aspect_ratio`, `invalid_prompt`, `invalid_instructions`, `invalid_temperature`, `invalid_max_tokens`, `unsupported_model`, `unknown_setting` and `invalid_config`. The frontend runs the same checks first.
  - **Storage:** only values that differ from the default are stored, so untouched nodes keep following the defaults. Old workflows without settings behave as before: first enabled model, workspace language and orientation, provider clip length.
  - **Snapshots:** a run copies the saved graph, settings included. Retries reuse that snapshot, so edits after the run change neither its history nor its retries (tested over HTTP and in the browser).
  - **Readiness:** invalid settings, a selected model that is disabled, a clip length the chosen video model does not support, and required inputs that nothing can fill are all reported before a run.
  - **Models:** a text or video step can pick any enabled workspace tool for its task. Auto picks the first one, as before.
- **Missing:**
  - **Viewing a past run shows the current settings:** the inspector edits the workflow, and the API does not return a run's snapshot. The step output shows what a video or text step used (provider, model, aspect ratio, duration, language).
  - **Settings for placeholder nodes** (script, image, voice, music, subtitle, render, publish) wait for their executors (M2).
  - **Resolution and audio** of the video request stay at each provider's defaults, and clip lengths are capped at 8 s (see P6).
  - **Option lists are fixed in code** (for example three languages). Adding one means a backend change and three translations.
- **Depends on:** F1, F10, F11, F12.

## Partially Implemented

### P1. Workflow execution (run semantics beyond the single-clip path)

- **Files:** `app/workflow/executor.py`, `app/workflow/nodes/video.py`, `app/workflow/nodes/review.py`, `app/workflow/nodes/text.py`, `app/main.py` (`start_workflow_run`, `approve_workflow_run`, `retry_workflow_run`), `app/video_worker.py`, `app/text_worker.py`.
- **Current:**
  - Run creation, advancing after a job, approval and retry all go through the executor (F10).
  - Multi-step chains work: text steps run one after another (or side by side on separate branches), each queued when its parents complete.
  - After approval, the steps after `review` are evaluated too. For example, a `publish` node becomes `blocked` by its placeholder.
  - Retry creates a new run from the original snapshot and the frozen video payload. It is allowed only for `blocked` or `failed` runs, and never after approval. Text steps in a retried run are generated and charged again.
    - The video request is frozen only when the video step starts together with the run. A video step that waits on a text step is built again from the snapshot's settings, with the new text and the current price.
    - Phase 3.5 fixed the frozen payload when a text branch starts beside the video (it used to freeze the text job).
- **Missing:**
  - One worker process per job kind (`video`, `text`, `image`). Images and scene clips share the child-job engine (`app/media_jobs.py`); text and single clips keep their own loops.
  - Clips made one per scene are joined only by a Render step (Phase 8); a Video step alone still outputs separate clips.
  - A retry regenerates every scene and image; successful files of the failed run are not reused, and there is no per-scene retry.
  - Credits for later steps are held only when those steps become ready. A run can therefore start and then block part-way on `insufficient_credits`; readiness shows the total up front.
  - Run cancellation and re-running a single step.
  - `approval_required = false` has no effect (U6).
  - Run-level timeouts beyond the video job age limit.
  - Running independent branches in parallel. Each pass is sequential within one transaction.
- **Depends on:** F10, F3. New executors declare their settings with `config_fields` (F13).

### P2. Node types

- **Files:** `app/workflow/registry.py` (`build_default_registry`), `app/workflow/nodes/*.py`, `app/main.py` (`NODE_TYPES`), `frontend/src/lib/workflow.ts` (`kindOf`, `nodeLibrary`, `EXECUTABLE`, `workflowTemplates`), `frontend/src/components/workflow/studio-node.tsx`.
- **Current:** all 20 node types the API accepts are registered, each with typed ports (F12). Eighteen of them do real work:

  | Type | Handler | Backend behavior | Notes |
  | --- | --- | --- | --- |
  | `idea` | `IdeaNodeHandler` | Completes locally | Output is `{title, topic}` |
  | `assets` | `AssetsNodeHandler` | Completes locally | Output lists every workspace asset; ports split it by media kind |
  | `scenes` | `ScenesNodeHandler` | Completes locally, free | Splits a script into `scenes` (Phase 3) |
  | `ai_writer`, `summarize`, `rewrite`, `translate`, `hook`, `title`, `cta` | Text handlers (F11) | Durable text job | Outputs `script` / `summary` / `text` / `hook` / `title` / `cta`, plus `provider`, `model`, `usage`, `language` |
  | `video` | `VideoNodeHandler` | Durable provider job per clip | One clip, or one per scene (Phase 5); model, aspect ratio, clip length and prompt override are node settings |
  | `image` | `ImageNodeHandler` | Durable provider job per image | One image per scene, or 1–4 from a prompt (Phase 4); Runway `gen4_image` |
  | `voice` | `VoiceNodeHandler` | Durable provider job per narration | One narration, or one per scene (Phase 6); Gemini TTS |
  | `subtitle` | `SubtitleNodeHandler` | Completes locally, free | SRT/WebVTT file asset timed by narration, clips or scenes (Phase 7) |
  | `render` | `RenderNodeHandler` | Durable local FFmpeg job | One final MP4 from clips, narration and subtitles (Phase 8) |
  | `review` | `ReviewNodeHandler` | `awaiting_review` when a parent created media, then the approve endpoint; otherwise `blocked` | Always manual |
  | `metadata` | `MetadataNodeHandler` (text) | Durable text job | YouTube title, description and tags as JSON (Phase 9) |
  | `publish` | `PublishNodeHandler` | Completes after Review as a hand-off; never uploads | Final video and prepared metadata for the Publishing page (Phase 9) |
  | `script`, `music` | `PendingAITaskHandler` | Always `blocked` | "Provider not connected" or "no AI tool selected" |
  | any other type | `UnsupportedNodeHandler` | `blocked`, `error.code = unsupported_node_type` | Only reachable from old snapshots |

  - **Library:** 60 entries. 19 map to backend types; the other 41 are "Sắp có" (U2).
  - **Templates:** 4 of 10 have runnable graphs. `social-video`, `youtube-short` and `tiktok-video` are all `idea → video → review`; the fourth is `blank`.
- **Missing:**
  - Real handlers for 2 types (`script`, `music`). Adding one means writing a `NodeHandler` subclass with its ports and registering it in place of its placeholder; `app/main.py` needs no change.
  - No template uses the text or scenes nodes yet.
- **Depends on:** F10, P1, F13, P5.

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
  - **Errors:** HTTP failures map to stable codes with a `retryable` flag and a shared category (`app/providers/errors.py`, Phase 3.6). A timeout, network error or 5xx during submit becomes `submission_unknown` and is never resubmitted automatically.
  - **Timeouts:** 10 s per API call; downloads connect in 30 s with 120 s per read.
  - **URL safety:** status, result and media URLs must match the provider's hosts, and redirects are not followed.
  - **Storing the result:** the worker downloads to a `.part` file, checks the size (at most 100 MB) and the MP4 box structure (`ftyp`, `moov`, `mdat`), then records the asset.
- **Missing:**
  - A shared adapter interface. Each module re-declares `ProviderError`, `VideoRequest`, `Submission` and the other types.
  - User control over duration, resolution and audio; ReelForge always sends the defaults above.
  - Image-to-video and reference inputs.
  - Square output: `video_orientation = square` blocks the run.
  - More than one model per provider, and per-model pricing.
  - Webhooks: the worker only polls, every 10 s.
  - Checks with live credentials for the remaining providers; Gemini and Runway passed according to the operator (see Live Provider Verification).
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

- **Files:** `app/main.py` (`AIToolInput`, `/api/ai-tools*`, `workflow_readiness`), `app/workflow/nodes/video.py`, `text.py` and `pending.py` (`readiness`), `app/providers/catalog.py`, `app/providers/text/__init__.py`, `app/models.py` (`AITool`), `migrations/versions/0005_ai_tools.py`, `frontend/src/app/models/page.tsx`.
- **Current:**
  - **Catalog:** each workspace has rows of `{task, provider, model, is_enabled}`, where `task` is `script`, `image`, `video`, `voice` or `music`. Only the owner can create, edit or delete them.
  - **What can run:**
    - A `video` row whose provider is in `VIDEO_PROVIDERS` and whose model is in that adapter's `VIDEO_MODELS`.
    - A `script` ("Text") row whose provider is `openai`, `anthropic` or `gemini`, with any model name the provider accepts (F11).
  - **Readiness:** each node's handler reports why a workflow cannot run, such as `missing_tool`, `tool_unavailable`, `missing_key`, `missing_config`, `invalid_config`, `experimental_disabled`, `unsupported_model`, `unsupported_aspect`, `unsupported_graph`, `insufficient_credits` or `unsupported_node`. Before that, the executor reports `invalid_settings` and `missing_input` (F13). `runnable` also requires the balance to cover the total.
  - **Per-node choice:** text and video steps can name a tool in their `tool_id` setting, picked in the inspector (F13).
  - **Credentials:** provider keys are environment variables set by the operator and shared by all workspaces.
  - **UI:** the models page offers five video and three text presets, and marks image, voice and music "config only".
- **Missing:**
  - A provider/model registry exposed by the API, with capabilities, pricing and parameters. The frontend hard-codes the presets and the runnable provider lists, and its "runnable" badge checks only the provider name, not the model.
  - Validation when a tool is saved. An unsupported model is accepted and fails only at readiness or run time (for text, when the provider answers 400/404).
  - Per-workspace credentials (bring your own key).
  - Providers for image, TTS and music.
- **Depends on:** nothing. F13 and M2 depend on it.

### P6. Credits reservation and usage tracking

- **Files:**
  - `app/usage.py`: `post_credit`, `consume`.
  - `app/workflow/nodes/video.py` (`VideoNodeHandler.execute` reserves credits) and `app/providers/catalog.py` (`video_credit_cost`).
  - `app/workflow/nodes/text.py` (`TextNodeHandler.execute` reserves credits) and `app/providers/text/__init__.py` (`text_credit_cost`).
  - `app/main.py`: `usage_overview`, `adjust_credits`.
  - `app/video_worker.py`: `_terminal_failure`, `_store_result`; `app/text_worker.py`: `_finish`.
  - `app/payments.py`.
  - Frontend: `frontend/src/app/billing/page.tsx` and the credit widget in `frontend/src/components/reelforge/app-shell.tsx`.
- **Current:**
  - **Ledger:** one balance per workspace, updated under a row lock, plus an append-only ledger. Each entry has a unique `reference`, so posting the same entry twice has no effect.
  - **Reservation:** a video run reserves a flat `VIDEO_CREDITS_PER_CLIP` (default 10) as `reserve:<run_id>`, in the same transaction as the run and its job. If the balance is too low, the API returns HTTP 402.
  - **Refunds:** safe pre-submit failures/definitive rejection automatically, or an explicit audited admin reconciliation (`refund:<run_id>`, once).
  - **Charging:** the reservation itself is the charge. A successful run adds one `UsageEvent` (`video:<step_id>`).
  - **Retries:** a retry reserves again under the new run ID, at the original frozen price.
  - **Text steps** (F11): a flat `TEXT_CREDITS_PER_GENERATION` is held per step (`text-reserve:<step>`) and finalized by one `UsageEvent` (`text:<step>`, units = total tokens on success). Deterministic failures refund (`text-refund:<step>`); uncertain outcomes require reconciliation since Phase 3.7.
- **Missing:**
  - Automatic authoritative provider billing reconciliation; Phase 3.7 supplies a manual audited resolution flow for uncertain charges (R1).
  - Pricing by model, duration or tokens. Because a clip costs the same whatever its length, the clip-length setting offers only 4, 6 and 8 s (the previous default was 8 s).
  - Trial and monthly grants: a new self-registered workspace starts with 0 credits.
  - Credit expiry.
  - Showing reserved and settled amounts separately in the ledger UI.
  - `usage.consume()` is unused by the app; both workers write `UsageEvent` directly.
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

- **Files:** `tests/` (32 modules, 262 tests), `.github/workflows/ci.yml`.
- **Current:**
  - **Unit tests:** jobs, providers, publishers, OAuth, the body limit, media maintenance, login throttling, the workflow engine, text generation, and typed ports.
  - **Runtime tests** (Phase 3.6, all offline):
    - `tests/test_runtime_support.py` (29):
      - the runtime file (parsing, no override, example names only, fingerprints);
      - the pre-flight for missing, configured, invalid and unsupported choices, with no key in the output;
      - the smoke commands refusing without live intent, and succeeding or failing with fake providers;
      - MP4 checks and the error categories of all five video adapters on submit;
      - explicit timeouts and log redaction;
      - `provider_detail` kept for logs, never stored in the step;
      - worker logs with IDs but no key or prompt, failures with refunds, and a rejected start.
    - `tests/test_workflow_provider_requests.py` (1): the Idea → AI Writer → Scene Splitter → Video → Review workflow over HTTP through the real workers with fake provider clients. It checks the arguments each provider receives against the node settings, the run report, and the log contents.
    - `tests/test_live_providers.py` (2): live smoke tests, skipped unless `REELFORGE_LIVE_TESTS=1`.
  - **Node settings tests** (`tests/test_node_config.py`, 17, Phase 3.5):
    - the schema served to the editor, valid settings, and a stable code for each invalid value;
    - defaults, the tool check, and video clip lengths per model;
    - settings reaching prompts, models and job payloads for text, scenes and video;
    - invalid stored settings blocking only their node, without charging;
    - a disabled model, and readiness for settings, required inputs and credits;
    - legacy workflows without settings;
    - one run over HTTP: catalog, save and reload, 422 codes, readiness, the snapshot freezing settings, a retry after edits, the retry payload fix, and the older run-dialog fields.
  - **Port tests** (`tests/test_workflow_ports.py`, 19, Phase 3):
    - legacy edge fallback and explicit source/target handles;
    - several parents feeding one input, and branching;
    - missing required inputs;
    - structured scenes passed from script to scenes to video;
    - invalid mappings rejected on save but tolerated in old snapshots;
    - input priority (edge, config, context) and Phase 2 output keys;
    - edge normalization and catalog consistency, scene splitting, and review passing its clip on;
    - one run over HTTP covering the catalog, validation and normalization.
  - **Text tests** (Phase 2):
    - `tests/test_text_providers.py` (12): request shape and normalization for OpenAI, Anthropic and Gemini over `httpx.MockTransport`; status-to-code mapping without secrets; transport errors; validation before any call; missing keys; the credit price setting.
    - `tests/test_text_nodes.py` (14): the AI Writer with its settings; Summarize, Rewrite and Translate reading upstream text; text flowing through writer → summarize → translate; list nodes; missing input or tool; provider rejection with a refund; transient retry and exhaustion; a crashed worker; missing keys; not enough credits at start and part-way; readiness; settings validation; and one run over HTTP.
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
- **Results after Phase 3.6** (2026-09-30, Windows, Python 3.14.7, Node 22.23.2):
  - `python -m unittest discover -s tests -v` → **Ran 262 tests, OK (skipped=5)**: the three skips below plus the two live tests. Earlier runs: 230 after Phase 3.5 (skipped=3), 213 after Phase 3, 194 after Phase 2, 168 after Phase 1, and 146 before Phase 1.
    - Phase 3.6 changed provider-test assertions for the renamed error codes, and the text-failure output, which now includes `category`.
    - Phase 3.5 changed seven older assertions: tone and language values must be listed codes, the 422 detail for settings is an object, prompts spell out tone and platform, the video output records aspect ratio and duration, the test echo handler declares its setting, and the scene-splitting test states its target duration.
    - Phase 3 changed five older assertions: AI Writer outputs are now under `script`, a connected idea beats `config.prompt`, the test source node declares a port, and `scenes` is a real node.
    - `PostgreSQLJobClaimTest.test_locked_job_is_skipped_by_another_worker` was skipped because `REELFORGE_TEST_DATABASE_URL` is not set.
    - `test_rejects_linked_root` and `test_symlinked_file_and_workspace_are_never_followed` were skipped because this Windows user cannot create symlinks.
    - One warning: Starlette's `TestClient` asks for `httpx2`, which is listed in `requirements-dev.txt`.
  - `npm run typecheck` → OK. `npm run build` → OK (Next.js 15.5.26; 23 app pages generated).
  - **Browser check (Phase 3.5):** a Playwright script, kept outside the repository, drove a production build against a separate API and SQLite database (47 checks passed). It covered the Idea → AI Writer → Scene Splitter → Video → Review workflow: settings, save and reload, undo and redo, duplicate and delete, typed connections, invalid values, advanced mode, the Run dialog, the run snapshot (read from the DB), a retry after later edits, and a legacy list workflow.
  - `npm audit` (first audit) reports 1 high and 1 moderate advisory, both from PostCSS pulled in by `next`. The fix requires Next 16.
- **Missing:**
  - PostgreSQL in CI. It is the production database, and its `SKIP LOCKED`, `FOR UPDATE` and `ON CONFLICT` code paths are untested.
  - Frontend unit, component and E2E tests in the repository (there is no test script; the Phase 3.5 browser check is not committed).
  - A live run against any provider: the opt-in smoke tests exist, but none has been run (see Live Provider Verification).
  - Tests for non-owner roles and for `PUT /api/settings/system`.
  - A concurrency test on PostgreSQL for two workers finishing sibling steps (the run-row lock is not exercised by SQLite).
  - Load tests for polling.
- **Depends on:** nothing.

### P9. Error handling

- **Files:** `app/main.py`, `app/workflow/executor.py` (`_evaluate`), `app/workflow/results.py` (`NodeError`, `RunRequestError`), `app/providers/*.py`, `app/providers/text/base.py` (`TextProviderError`, `http_error`), `app/publishers/*.py`, `app/video_worker.py`, `app/text_worker.py`, `app/youtube_worker.py`, `frontend/src/lib/errors.ts`, `frontend/src/components/workflow/types.ts`, `frontend/src/lib/i18n/*.ts`.
- **Current:**
  - **Provider and publisher errors:** typed, with a stable `code`, a `retryable` flag and `http_status`. Text and video provider errors also carry a shared `category` (Phase 3.6). They never include secrets or response bodies. Text adapters also turn empty output, refusals and safety blocks into `empty_output` or `content_blocked`, instead of saving a blank step.
  - **Workers:** turn those errors into step or publication states with readable detail.
  - **Node errors:** a handler can return a `failed` result with a `NodeError(code, message, retryable)`, which is stored as `output.error`. An unexpected exception inside a handler becomes a `handler_error` failure on that step. It is logged through `logging.getLogger("app.workflow.executor")`, the first use of `logging` in `app/`.
  - **API:** `HTTPException` with English `detail` strings. Handlers raise `RunRequestError`, which the API turns into the same HTTP status and message.
  - **Readiness:** returns stable status codes, which the frontend localizes; settings problems also carry `code` and `field`.
  - **Settings errors:** a 422 for node settings carries a stable `code`, the `field` and the `node_id`, and the frontend translates by code (F13).
- **Missing:**
  - Stable error codes on other HTTP responses. Except for settings errors, the frontend translates by exact text: English `detail` strings through `t.errors.server[...]` and Vietnamese step `detail` strings through `t.details[...]` (see D6).
  - Structured logs in the YouTube worker and in the API routes outside workflow execution. Phase 3.6 added them to the executor and the text and video workers, and configures logging in every process.
  - A global exception handler and request IDs.
  - Configuration errors that still return 500 instead of a clear message:
    - An invalid `WORKSPACE_MEDIA_QUOTA_BYTES`, or an invalid `VIDEO_CREDITS_PER_CLIP` during readiness, raises `RuntimeError`. During a run, the same `VIDEO_CREDITS_PER_CLIP` error now fails the video step instead.
    - An asset row whose file is missing on disk makes `FileResponse` fail.
  - A worker that keeps crashing has no backoff or alerting.
- **Depends on:** nothing.

### P10. Retry behavior

- **Files:** `app/main.py` (`retry_workflow_run`), `app/video_worker.py`, `app/text_worker.py` (`run_one`, `MAX_ATTEMPTS`), `app/youtube_worker.py` (`_transient_retry_delay`), `app/publications.py` (`can_retry_publication`, `retry_publication`), `app/main.py` (`refresh_payment_order`).
- **Current:**
  - **Runs:** `POST /api/workflow-runs/{id}/retry` creates a new run (with `retry_of_id`) from the original snapshot, settings included, and the frozen video payload (the run's `video:` job). Only `blocked` or `failed` runs qualify, and never after approval.
  - **Text jobs:** definitive rate-limit rejections retry with backoff 5 s × 2ⁿ, at most 3 attempts, then refund. Deterministic failures refund at once. Uncertain requests and reclaimed running leases require reconciliation and are not automatically sent again (Phase 3.7).
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
  - Retrying an unresolved or confirmed-charge `needs_attention` run remains intentionally prohibited. After an admin refund, safe asset-free runs can use the existing retry endpoint (R1).
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
    - Other secrets live in the bootstrap file or the environment, never in SQL. Text provider keys are read from the environment when a job runs and are never logged, stored, or included in errors. Gemini's key is sent in a header, not the URL.
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

Phase 16 resolved U1–U5: each control was implemented or removed, and no "Sắp có" (coming soon) control remains. [FINAL_PRODUCT_AUDIT.md](FINAL_PRODUCT_AUDIT.md) lists every decision; `tests/test_product_audit.py` keeps it that way.

### U1. AI tool pages (resolved: removed)

- `/ai/{writer,image,video,repurpose,movie-recap}` redirect to `/create`; `ai-tool.tsx` was deleted. The same features are workflow templates.

### U2. Node library entries and templates without a backend type (resolved)

- Every library entry adds an executable step, either a node type or a preset of one (for example Short script = AI Writer, 60 s). Items without a backend (voice clone, sound effects, audio mixer, timeline, transitions, overlays, crop, stock media, image-to-video, research, storyboard, scene planner, YouTube URL, download) are not listed.
- Every template builds a backend graph: Movie Review, Article → Video and Product Video became real templates.

### U3. Previews for nodes that do not execute (resolved)

- Every listed node executes and its preview shows its output; the Background Music step plays its chosen track. Only the legacy `script` type (kept so old workflows open) still blocks, and the inspector asks to replace it.

### U4. Project brief and workspace editor (resolved: removed or real)

- The brief chips, AI rewrite actions, storyboard tab and assistant panel were removed. The latest script is real (`GET /api/scripts`).

### U5. Placeholders in settings, channels, publishing, billing, library, media and calendar (resolved)

- Settings: display name, content defaults and the default publishing time are real; teammates, 2FA, retention, auto-schedule and the unused toggles were removed.
- Channels and Publishing: YouTube, TikTok and Facebook are real; Instagram is hidden.
- Billing: card payment (OnePAY) is real when configured.
- Library: the Scripts tab is real. Media: "Add to project" is real. Calendar: real scheduling by visible range.

### U6. The `approval_required` workspace setting

- **Files:** `app/main.py` (`WORKSPACE_DEFAULTS`, `WorkspaceSettingsInput`, `update_workspace_settings`), `frontend/src/app/settings/page.tsx`.
- **Current:** the owner can edit it and it is saved, but no code reads it. A review node always requires manual approval, and a graph without one never does.
- **Missing:** engine logic that honors it (for example, skipping or requiring review).
- **Depends on:** P1.

## Missing

### M1. Per-node settings UI (done in Phase 3.5)

- **Current:** done; see F13. The inspector edits every executable node's settings from the schema in `GET /api/workflow-node-types`, and the video model, aspect ratio, clip length and prompt are node settings.
- **Missing:** showing a past run's snapshot settings in the inspector, and settings for the placeholder nodes (F13).
- **Depends on:** F10, F11.

### M2. Executors for script, image, voice, music, subtitle, render and publish

- **Files to change:** new handlers in `app/workflow/nodes/` registered in `app/workflow/registry.py`, a generalized worker (today `app/video_worker.py` and `app/text_worker.py`) that reports through `WorkflowExecutor.finish_step`, and new provider modules.
- **Current:** image generation (Phase 4), one clip per scene (Phase 5), voice (Phase 6), subtitles (Phase 7) and FFmpeg render (Phase 8) are done; see [Phase 4 and 5 changes](#phase-4-and-5-changes) and [Phase 6, 7 and 8 changes](#phase-6-7-and-8-changes). Metadata and the Publish hand-off came in Phase 9. The two other node types have placeholder handlers that always return `blocked`, but they already declare their ports (F12). Text generation (F11) and rule-based scene splitting (F12) exist.
- **Missing:**
  - Script generation: `script` can subclass `TextNodeHandler`. That is a product decision, since it would start charging existing workflows.
  - An AI scene splitter with better visual prompts, using JSON output from the text layer, in place of the rule-based one.
  - Music generation and mixing a music bed under the narration.
  - Subtitles aligned to speech (speech recognition or forced alignment); Phase 7 times them by narration, clip or scene length.
  - Image slideshows and transitions in Render; Phase 8 joins video clips only.
  - Publishing to other channels (TikTok, Facebook) and scheduled publishing; YouTube publishing stays a human action after approval (Phase 9).
  - A price and a job kind for every paid executor.
- **Depends on:**
  - F13 (done: settings declared with `config_fields` appear in the inspector without frontend changes).
  - F10 (done: registry, input resolution, job requests, `finish_step`), plus a generic worker loop for new job kinds (P1).
  - P5 (providers) and P6 (credits).
  - P4, for render inputs and outputs.

### M3. Scheduling and more channels

- **Files to change:** `app/publications.py`, new workers modeled on `app/youtube_worker.py`, `app/publishers/{facebook,tiktok}.py`, `frontend/src/app/{calendar,channels,publishing}/page.tsx`.
- **Current:** YouTube uploads only (private, unlisted or public), queued when the owner presses Publish after approval.
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
- **Current:** Phase 3.7 adds admin reconciliation/history, exact refunds, charge confirmation and provider uncertainty policy. Other credits arrive through payment or a manual admin adjustment.
- **Missing:**
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
- **Current:** structured JSON logs for workflow execution in the API and in the text and video workers, with IDs, latencies and error categories, and a `process_started` line per process (Phase 3.6). Beyond that, the only visibility is state in the DB.
- **Missing:**
  - Log shipping and retention (journald only), request IDs, and structured logs in the YouTube worker and the other API routes.
  - Metrics: queue depth, job latency, provider errors and spend.
  - Alerting and an admin view of jobs and the queue.
  - Automated backup and restore for the database and media.
- **Depends on:** nothing.

### M10. Frontend tests

- **Files to change:** `frontend/package.json` and new test files.
- **Current:** CI runs only typecheck and build. Phase 3.5 was checked with a Playwright script run by hand (P8); it is not in the repository.
- **Missing:**
  - Component tests for the editor's status mapping, run actions and settings fields (`node-config.ts` is pure functions and easy to test).
  - A committed browser test of the settings flow.
  - An E2E happy path (create → run → approve → publish) against a stubbed API.
- **Depends on:** nothing.

## Technical Debt

| ID | Debt | Where | Impact |
| --- | --- | --- | --- |
| D1 | One 1,202-line module (1,372 before Phase 1) holds 45 routes, business logic and settings access. Run and node logic moved to `app/workflow/` and provider wiring to `app/providers/catalog.py`. | `app/main.py` | Both workers still import `app.main` (for `media_root`, `MAX_UPLOAD` and the quota) and so run its import-time side effects. |
| D2 | Importing the modules has side effects: the migration check and settings seeding in `app.main`, and the DB URL resolution in `app.db` | `app/main.py` (module level), `app/db.py` | Any import (tests, workers, tooling) needs a configured, migrated database. |
| D3 | Every video adapter re-declares the same types (only the error base class and the status mapping are shared since Phase 3.6). The text adapters share one base class (Phase 2). The backend has one provider map per modality, but the frontend repeats both. | `app/providers/*.py`; `app/providers/catalog.py`; `app/providers/text/__init__.py`; `VIDEO_PROVIDERS`, `TEXT_PROVIDERS` and the presets in `frontend/src/app/models/page.tsx` | Adding a provider still means editing the backend and the frontend separately. |
| D4 | The frontend copies backend constants and hand-writes the API types. Ports and node settings are the exception: they come from `GET /api/workflow-node-types` (Phases 3 and 3.5). | `frontend/src/lib/workflow.ts` (`NODE_TYPES`, `EXECUTABLE`, `TEXT_NODES`, `MAX_NODES`), `frontend/src/lib/studio.ts` (`ACCEPTED_UPLOADS`, `MAX_UPLOAD_BYTES`), `frontend/src/lib/types.ts` | Drift goes unnoticed, since nothing is generated from OpenAPI. `EXECUTABLE` could come from the catalog too. |
| D5 | Some models are defined outside `app/models.py` | `app/auth_security.py`, `app/publishers/google_oauth.py`, `app/publications.py` | Complete metadata depends on the imports in `migrations/env.py`. |
| D6 | The backend returns user-facing Vietnamese text, and the frontend translates it by exact-text lookup | `app/workflow/nodes/*.py`, `app/workflow/executor.py`, `app/main.py`, `app/video_worker.py`; `details` and `errors.server` in `frontend/src/lib/i18n/{vi,en,ja}.ts` | Rewording any backend message silently breaks its translation. `NodeError.code` gives new failures a stable code, but only settings errors and readiness are translated by code so far. |
| D7 | Endpoints and step outputs are not paginated | `/api/dashboard` returns every project, asset and workflow (with graphs); the `assets` node output embeds every asset (`ExecutionContext.assets` now loads them only when an `assets` node runs) | Cost grows with workspace size for both page loads and runs. |
| D8 | `attempt_count` increases on every poll, and leases have no heartbeat | `app/jobs.py`, `app/video_worker.py` | Misleading counts, and long downloads get redone. |
| D9 | Uploads are spooled twice (middleware, then multipart parser), and the workspace row stays locked while the file is written | `app/body_limit.py`, `app/main.py` (`upload_asset`) | Twice the disk I/O, and concurrent uploads to one workspace run one at a time. |
| D10 | Settings are read on every request (`same_origin` opens its own DB session) | `app/main.py` (`same_origin`, `setting`) | An extra DB round trip on every mutation. |
| D11 | The legacy `workspaces.plan` column is kept in sync by hand, and the plan order is hard-coded | `app/main.py` (`billing_checkout`, `update_subscription`), `app/payments.py` | The two can disagree if one code path is missed. |
| D12 | The model and the migrations disagree (`uq_workflow_run_node`), and CI has no `alembic check` | `app/models.py`, `migrations/versions/0006_workflow_runs.py` | Autogenerate would propose dropping the constraint. |
| D13 | Two ways to record usage | `app/usage.py` `consume()` is used only by tests; the worker writes `UsageEvent` directly | Future executors may pick different paths. |
| D14 | Advisory in the frontend dependencies | `frontend/package-lock.json` (PostCSS via Next 15.5; `npm audit`: 1 high, 1 moderate) | Affects the build toolchain; the fix needs Next 16. |
| D15 | Toolchain versions differ between CI and dev | CI uses Python 3.13 and Node 20; this audit ran on Python 3.14 and Node 22. `TestClient` warns unless `httpx2` from `requirements-dev.txt` is installed. | Something can pass in one environment and fail in the other. |
| D16 | One worker process per job kind, each with its own claim loop, lease and retry code | `app/video_worker.py`, `app/text_worker.py`, `app/youtube_worker.py` | Each new kind adds a systemd unit and another copy of the loop. |
| D17 | Prompt templates live in Python strings in the handlers | `app/workflow/nodes/text.py` | Changing wording needs a deploy, and templates are not versioned in run snapshots (the built prompt is, inside the job payload). |
| D18 | Option labels are translated per field label and option value | `config.options` in `frontend/src/lib/i18n/{vi,en,ja}.ts`, `config_fields` in `app/workflow/nodes/*.py` | A new option without a translation shows its raw code; nothing checks that the two sides agree. |

## Critical Risks

### R1. Uncertain paid jobs hold credits (resolution added in Phase 3.7)

- **Where:** `app/video_worker.py`, every call to `_terminal_failure(..., refund=False)`:
  - The provider reports that generation `failed`.
  - The submit outcome is unknown: a network error or 5xx on the POST, or the worker stopped while the step was `submitting`.
  - Polling failed with 3 errors in a row.
  - Downloading or validating the result failed.
  - The workspace media quota is full after the provider has already finished (`_store_result`).
- **Effect:**
  - The run and step become `needs_attention` and the reserved credits are kept.
  - The run cannot be retried: retry accepts only `blocked` or `failed`.
  - System admins can now resolve it using an audited refund or charge confirmation in Admin → Reconciliation. Old unresolved incidents are included when their reservation is valid.
  - Ordinary provider failures, such as a content-moderation rejection or an outage, take this path.
- **Mitigation:** Phase 3.7 implements M6 reconciliation and the pre-submit quota check. Provider evidence and timely operator review remain required; storage can still fill after preflight.

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

### R9. Little observability

Since Phase 3.6, workflow execution and the text and video workers write structured logs, but nothing collects them or alerts on them. There are no metrics for queue depth, latency or provider errors. Users will still notice a crashed worker, an expired provider key or a full disk before operators do, unless someone reads the logs.

### R10. No handling for loss or rotation of the encryption key

If `REELFORGE_TOKEN_ENCRYPTION_KEY` is lost or changed, no stored YouTube connection or resumable upload session can be decrypted. In-flight uploads become `needs_attention`. The code does not support key rotation (for example with `MultiFernet`).

### R11. Text credits are not tied to provider cost

- **Where:** `app/providers/text/__init__.py` (`text_credit_cost`), `app/workflow/nodes/text.py`, `app/text_worker.py`.
- **Problem:** a text step costs a flat `TEXT_CREDITS_PER_GENERATION` (default 1), whatever the model, prompt length (up to 60,000 characters) or `max_tokens` (up to 8,192). Phase 3.7 limits automatic retries to definitive rate-limit rejections and reconciles ambiguous results; it does not implement token-based pricing.
- **Effect:** with an expensive model (for example the Claude Opus preset) and the default price of 1, provider spend can exceed what credits recover.
- **Direction:**
  - Set the price per deployment, and alert on provider spend.
  - Move to token-based settlement: reserve a maximum, charge from `usage` (already recorded as the event's units), refund the difference.
  - Rate-limit text jobs per workspace.

### R12. Additional providers still require live verification

All text and video tests use mocked HTTP or fake clients. The request and response shapes follow the vendors' public API references. Model names, token limits, reasoning-model behavior, video durations, result URLs and download hosts still need a smoke test with real keys before users rely on them. For example, reasoning models can spend the whole token budget before writing any text, which surfaces as `empty_output`, and a provider may serve results from a host the adapter does not allow.

The operator confirmed Gemini, Runway `gen4.5` and the complete text-to-video review workflow before Phase 3.7. Other providers still need live tests (see Live Provider Verification).

### R13. Every process that advances runs needs every provider key

- **Where:** `app/text_worker.py` and `app/video_worker.py` call `WorkflowExecutor.finish_step`, which starts the next steps in the worker's own process.
- **Problem:** the next step checks its provider key in that process. For example, the text worker queues the video step after an AI Writer, and blocks it with "Server cần RUNWARE_API_KEY." if only the API and video worker have that key. The Phase 3.5 browser check hit this with a simulated worker.
- **Mitigation (Phase 3.6):** every process loads the same runtime file (`.env.runtime`, or `EnvironmentFile=/etc/reelforge/runtime.env` in production) and logs `process_started` with key fingerprints, so a mismatch shows in the logs.
- **Still open:** a process started by hand with its own environment can still differ. Moving provider checks for later steps into the worker that runs them would remove the dependency.

## Recommended Implementation Order

Guiding rules:

- Keep the working path `topic → clip → review → YouTube` passing tests at every step.
- Fix the money and security risks before adding executors.
- Build the node-configuration and executor foundation once, then add node types on top of it.
- As each backend capability lands, replace the matching "Sắp có" state in the existing UI rather than redesigning it.

| Step | Work | Resolves | Depends on |
| --- | --- | --- | --- |
| 0 | Operational hardening: <br>• structured logging in the API and workers (**done for workflow execution and the text and video workers in Phase 3.6**) <br>• a PostgreSQL service in CI with `REELFORGE_TEST_DATABASE_URL` <br>• `alembic check` in CI <br>• an enforced production checklist: `create-admin` before exposure, HTTPS origin, `secure_cookies`, forwarded-IP handling for the throttle <br>• frontend smoke tests for the editor's status mapping | R2–R5, R9, D12, part of M10 | — |
| 1 | **Credit reconciliation done in Phase 3.7:** admin API/UI and audit history, conservative provider policy and pre-submit quota check. **Still open/out of phase scope:** trial and monthly grants, complex pricing | R1, part of R6, M6 | 0 |
| 2 | Split `app/main.py` into routers and services without changing behavior <br>• remove the import-time side effects <br>• return stable error codes instead of translating by text | D1, D2, D6, P9 | 0 |
| 3 | Provider/model registry: <br>• one adapter protocol <br>• one registry used by the API and worker and exposed to the frontend (capabilities, defaults, price) <br>• validate AI tools when they are saved | D3, D4, P5 | 2 |
| 4 | Per-node configuration. **Done in Phase 3.5 (F13):** a settings schema served with the ports, inspector fields for every executable node, video model, aspect ratio, clip length and prompt in `config`, validation codes, readiness. **Still open:** <br>• show a past run's snapshot settings <br>• settings for new executors as they land | M1 | 3 |
| 5 | Engine refactor. **Done in Phase 1 (F10):** executor, registry, handlers, input resolution, job requests, `finish_step`, advancing after approval. **Done in Phase 3 (F12):** typed ports and required inputs. **Still open:** <br>• a generic worker loop for new job kinds <br>• honor `approval_required` <br>• run cancellation <br>• more than one paid node per run | P1, U6, M4 | 4, F3 |
| 6 | Text generation. **Done in Phase 2 (F11):** OpenAI/Anthropic/Gemini adapters, text worker, and seven text nodes with credits. **Phase 3.6:** smoke-test tooling for text and video. **Still open:** <br>• running the smoke tests with live keys (R12) <br>• token-based pricing (R11) <br>• feed upstream text into the video prompt <br>• decide whether `script` becomes a text node <br>• `scenes` with JSON output <br>• text-node templates | Part of M2, U4, R11, R12 | 5, 3, P6 pricing |
| 7 | Asset lifecycle: <br>• delete and rename <br>• link uploads to projects <br>• ffprobe metadata <br>• a storage abstraction <br>• backups | M5, P4, R6 | 0 |
| 8 | `image`, `voice`/`music` and `subtitle` executors, then the FFmpeg `render` node | M2, U3 | 5, 6, 7 |
| 9 | `publish` node executor (queues a publication after approval), then scheduling (`available_at` plus calendar), then TikTok and Facebook connections built on the existing adapters | P7, M3, U5 | 5, F4 |
| 10 | Teams, roles and workspace switching; account security (password reset, 2FA); full frontend E2E tests | M7, M8, M10 | 2 |

Phase 1 took the engine part of step 5 ahead of steps 2–4, because it did not depend on them. Phase 2 then did most of step 6 and the backend half of step 4, Phase 3 connected the nodes with typed data, Phase 3.5 finished step 4, and Phase 3.6 added the runtime environment, logs and smoke tooling. The next highest-value work:

- Preserve the operator-confirmed Gemini/Runway/full-workflow live results; verify additional providers when requested (R12). Complex text pricing (R11) remains outside Phase 3.7.
- Deploy migration 0011 and use the reconciliation procedure for existing attention incidents. Step 1's reconciliation work is complete; grants/pricing remain separate. Further capabilities require a new phase.
