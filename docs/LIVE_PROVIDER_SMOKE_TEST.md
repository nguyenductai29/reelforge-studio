# Live Provider Smoke Test

## Purpose

The normal test suite never calls a provider: every provider test uses mocked HTTP or fake clients. This guide proves that the real pipeline works with real accounts, in three steps:

1. A **pre-flight check** confirms configuration. It makes no network request.
2. **Direct smoke tests** send one small request to one text provider and one video provider.
3. A **full workflow test** runs `Idea → AI Writer → Scene Splitter → Video → Review` through the API, the workers and the editor.

Run only one request per modality. Repeat a request only after fixing a clear configuration or implementation problem.

Keys stay on the server. They are read from environment variables and never stored in the database, sent to the browser, written to step outputs, job payload responses, API errors or logs, or printed by these tools. The tools show an 8-character fingerprint instead of a key, so you can compare processes without revealing it.

## Required Environment Variables

Copy `.env.runtime.example` to `.env.runtime` in the repository root and fill in only what you test. The real file is git-ignored.

```bash
cp .env.runtime.example .env.runtime        # PowerShell: Copy-Item .env.runtime.example .env.runtime
```

Every process loads the same file at startup:

- the API, in its lifespan;
- `python -m app.text_worker`, `app.video_worker` and `app.youtube_worker`;
- `python -m app.provider_check` and `app.smoke_test`.

`REELFORGE_ENV_FILE=/path/to/file` selects another file. A variable already set in the process wins over the file. The format is systemd's `EnvironmentFile`: `KEY=value` lines, `#` comments and no `export`, so production can point every unit at the same file.

| Purpose | Variables |
| --- | --- |
| Text provider (one of) | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` |
| Video provider (one of) | `FAL_KEY`, `RUNWARE_API_KEY`, `REPLICATE_API_TOKEN`, `RUNWAYML_API_SECRET` + `RUNWAY_OUTPUT_HOSTS` |
| Choice for the smoke tests (optional) | `REELFORGE_SMOKE_TEXT_PROVIDER`, `REELFORGE_SMOKE_TEXT_MODEL`, `REELFORGE_SMOKE_VIDEO_PROVIDER`, `REELFORGE_SMOKE_VIDEO_MODEL` |
| Prices in credits (optional) | `VIDEO_CREDITS_PER_CLIP` (default 10), `TEXT_CREDITS_PER_GENERATION` (default 1) |
| Logs (optional) | `REELFORGE_LOG_FORMAT` (`json` or `text`), `REELFORGE_LOG_LEVEL` (default `INFO`) |
| Permission to spend | `REELFORGE_LIVE_TESTS=1`, set in the shell for one command. Setting it in `.env.runtime` has no effect. |

Without a smoke choice, the tools pick the first provider whose key is set.

## Supported Text Providers

| Provider | Key | Smoke-test default model | Notes |
| --- | --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `gpt-4.1-mini` | Chat Completions. Temperature is sent only when set. |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` | Messages API. The Models page preset is `claude-opus-5-5`. |
| Gemini | `GEMINI_API_KEY` | `gemini-2.5-flash` | `generateContent`. The key goes in a header, never in the URL. |

Any model name the provider accepts can be passed with `--model`. Names outside the presets are reported as "accepted", not "recognized".

## Supported Video Providers

| Provider | Key | Model | Cheapest smoke request |
| --- | --- | --- | --- |
| fal | `FAL_KEY` | `fal-ai/veo3.1/fast` | 4 s, 720p, no audio |
| Runware | `RUNWARE_API_KEY` | `bytedance:seedance@2.5` | 4 s, 480p, no audio |
| Replicate | `REPLICATE_API_TOKEN` | `google/veo-3.1-fast` | 4 s, 720p, no audio |
| Runway | `RUNWAYML_API_SECRET`, `RUNWAY_OUTPUT_HOSTS` | `gen4.5` | 2 s, 720p (no audio model) |

Dola is an experimental gateway (`DOLA_EXPERIMENTAL_ENABLED`, `DOLA_BASE_URL`, `DOLA_API_KEY`) and is not part of this test.

Workflow runs use the node's clip length: 4, 6 or 8 s, or the model default of 8 s. The smoke test uses the shortest length the model accepts.

## Pre-flight Check

```bash
python -m app.provider_check
python -m app.provider_check --text openai --text-model gpt-4.1-mini --video runware
python -m app.provider_check --only video
```

Example output:

```text
Runtime environment: C:\...\reelforge-studio\.env.runtime

Text provider: openai
Model: gpt-4.1-mini (recognized)
API key: OPENAI_API_KEY configured (fingerprint 3f2a91c0)

Video provider: runware
Model: bytedance:seedance@2.5 (recognized)
API key: RUNWARE_API_KEY configured (fingerprint 8be01d77)
Smoke request: 4s, 480p, 16:9, no audio

Ready for smoke test.
```

Every problem is listed with what to fix. The exit status is 0 when everything checked is ready, and 1 otherwise.

## Direct Text Smoke Test

```bash
REELFORGE_LIVE_TESTS=1 python -m app.smoke_test text
python -m app.smoke_test text --live --provider anthropic --model claude-haiku-4-5-20251001
```

Without `--live` or `REELFORGE_LIVE_TESTS=1`, the command refuses (exit 3) before loading any key.

It sends one request: "Write one short sentence explaining why consistent posting helps a social media channel." with at most 256 output tokens (`--max-tokens`). It prints:

- the provider, the requested and returned model;
- the latency;
- input, output and total tokens;
- the finish reason;
- a preview of at most 160 characters.

It ends with `PASSED` (exit 0). On a failure it prints the error category, code and HTTP status, the provider's own message (`Provider said: …`, at most 200 characters) and a hint (exit 1). It never retries.

## Direct Video Smoke Test

```bash
REELFORGE_LIVE_TESTS=1 python -m app.smoke_test video
python -m app.smoke_test video --live --provider fal --aspect 9:16 --timeout 900
```

The prompt is "A cinematic sunrise over a quiet mountain lake, slow camera movement." with the cheapest settings in the table above. The command:

1. submits the job and prints the provider's job ID;
2. polls every 10 s (`--poll`) until the job completes or fails, or `--timeout` passes (900 s by default);
3. downloads the result, checking the host allow-list, the content type, the 100 MB cap and the MP4 structure;
4. saves it to `instance/smoke-tests/<provider>-<UTC time>.mp4` (git-ignored; `--output` for another folder).

It then prints the provider, model, job ID, requested and actual duration, file size and elapsed time.

The file is not added to workspace media. A job still running at the timeout is **not cancelled** at the provider; check its dashboard.

To run both through unittest: `REELFORGE_LIVE_TESTS=1 python -m unittest tests.test_live_providers -v` runs the text test. Add `REELFORGE_LIVE_VIDEO=1` for the video test. Without the flags, both are skipped.

## Full Workflow Smoke Test

Start each process in its own terminal from the repository root. Each one loads `.env.runtime` itself, so no terminal needs exports. On Windows, run the `.venv\Scripts\Activate.ps1` line in each terminal first, or use the Python that has the dependencies.

```bash
# Terminal 1: API
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Terminal 2: text worker
python -m app.text_worker
# Terminal 3: video worker
python -m app.video_worker
# Terminal 4: frontend
cd frontend && npm run dev
# Terminal 5 (only to publish): YouTube worker
python -m app.youtube_worker
```

Each process logs one `process_started` line with the file it loaded and a fingerprint per provider key. The fingerprints must match across the API and both workers. A worker that finishes a step also starts the next one: the text worker queues the video step after the AI Writer, so it needs the video key too.

Steps (the UI labels are the English ones; Vietnamese is the default language):

1. Start the API.
2. Start the frontend and open `http://localhost:3000`. Use that exact origin: it is the default `frontend_origin`, and the API rejects changes from any other origin (403 "Invalid origin").
3. Start the text worker.
4. Start the video worker.
5. Sign in.
6. Make sure the workspace has credits: one text step costs `TEXT_CREDITS_PER_GENERATION` and one clip `VIDEO_CREDITS_PER_CLIP`, so 11 by default. An administrator adds credits on the Admin page.
7. Under **AI Models**, add and enable one real Text model (for example OpenAI `gpt-4.1-mini`).
8. Add and enable one real video model (for example Runware Seedance 2.5).
9. Create a project.
10. Give it a topic, for example "Sunrise over a quiet mountain lake".
11. Create or open a workflow with `Idea → AI Writer → Scene Splitter → Video → Review`, connected port to port: Topic → Prompt, Script → Script, Scenes → Scenes, Video → Media.
12. Select **AI Writer** and set Language, Tone, Target platform, Target duration, Additional instructions and AI model in the inspector.
13. Select **Video** and set AI model, Aspect ratio, Clip duration (4 s keeps the cost down) and, optionally, Prompt override.
14. Click **Save**. The header shows "Saved".
15. Before running, each node shows **Ready**, and the Run dialog lists no problems and shows the credit estimate. Click **Run workflow**, pick the project, then **Start run**.
16. **AI Writer** goes **Queued → Running → Completed**, and its text appears on the node and in the inspector. Idea is Completed immediately.
17. **Scene Splitter** becomes **Completed** as soon as the script arrives. It is free and runs on the server.
18. **Video** becomes **Queued**. The editor polls every 5 s.
19. The video worker submits the job: the step shows **Running** until the provider finishes (usually a few minutes), then **Completed**.
20. The MP4 preview plays on the Video node and in the inspector.
21. **Review** becomes **Needs Review**, and the run shows the same status.
22. Click **Approve video**. Review becomes **Completed** and the run **Completed**.

Expected node states, in order:

| Node | States |
| --- | --- |
| Idea | Ready → Completed |
| AI Writer | Ready → Queued → Running → Completed |
| Scene Splitter | Ready → Waiting → Completed |
| Video | Ready → Waiting → Queued → Running → Completed |
| Review | Ready → Waiting → Needs Review → Completed (after approval) |

API step statuses are `queued`, `submitting`, `running`, `completed` and `awaiting_review` (shown as Needs Review). `skipped` is shown as Waiting.

Then confirm that the node settings reached the providers:

```bash
python -m app.smoke_test run-report <run_id>
```

The run ID is in the editor URL (`?run=…`) after starting a run, or in the History sheet. The report makes no provider call; it reads the run from the database configured in `instance/bootstrap.json`. For each queued job, it compares the settings frozen in the run snapshot with the request that was sent:

- the tool and model;
- the language, and the tone, platform, length and style phrases in the prompt;
- `max_tokens` and `temperature`;
- the aspect ratio, clip length and prompt override.

It prints `consistent` or each mismatch. The same comparison appears in the logs: every `workflow_step_queued` event carries the node's `settings` (free text by length only) next to the `job` fields sent to the provider.

## Expected Costs

These tests incur provider charges; this repository does not know your prices.

- **Text:** one generation of at most 256 output tokens with a small model. Usually a negligible cost.
- **Video:** one generation at the shortest duration and lowest resolution. This is the expensive part: text-to-video models bill per second of output, often with a minimum per request. Check the provider's current pricing before running.
- **Workflow test:** one text generation plus one clip. The node's shortest clip length is 4 s.
- **ReelForge credits** are separate from provider charges. They are debited in the workspace: 1 + 10 by default.
- **Retries cost again.** A retried run generates its text steps again.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| **401 / `authentication_error`** | The key is wrong, revoked or for another provider. Compare the fingerprint from `app.provider_check` with the key you expect. Look for quotes or whitespace in `.env.runtime`; the check reports a key containing spaces as invalid. |
| **402 / `billing_error`** | The provider account has no credit or no billing set up. |
| **429 / `rate_limited`** | Wait, then retry once. A text step is attempted up to 3 times by itself (retrying after 5 s, then 10 s). A video submit is never retried automatically: the step fails, its credits are refunded, and you use **Retry**. |
| **Unsupported model** (`unsupported_model`, or `invalid_request` / HTTP 404 from the provider) | Text: check the exact model name for your account. Video: each adapter accepts only the models in the table above. In a workflow, readiness shows "Model video này chưa được hỗ trợ" before running. |
| **Empty text output** (`empty_output`) | Reasoning models spend tokens before answering. Raise `--max-tokens`, or raise **Max tokens** on the node in Advanced mode. |
| **Provider timeout** (`timeout`) | Text requests wait at most 120 s (connect 10 s). Video API calls wait 10 s each. A timed-out **submit** is `submission_unknown`: the provider may have accepted the job, so it is not retried and its credits stay held (the step shows it needs checking). Check the provider dashboard before trying again. |
| **Video job stuck** | The step stays Running while the provider works. The worker polls every 10 s and gives up after `VIDEO_JOB_MAX_AGE_SECONDS` (default 6 h). Look for `provider_request_completed` events with `operation: status` in the video worker log: a changing `state` means the provider is progressing. No events at all means the video worker is not running. |
| **Download failed** | The result URL host must be one the adapter allows (for Runway, one of `RUNWAY_OUTPUT_HOSTS`), the response must be `video/mp4` under 100 MB, and the file must be a complete MP4. Such failures end the step as needing attention, and the credits stay held until an administrator resolves them. |
| **Workspace has insufficient credits** | The run is rejected with HTTP 402 when it starts, or a later step shows "Không đủ credits cho bước này." Add credits on the Admin page, then start a new run or retry. |
| **Worker not running** | Steps stay **Queued** with no `job_claimed` event. Start `python -m app.text_worker` / `python -m app.video_worker`; `--once` processes a single job. |
| **Wrong environment in one process** | A step blocks with "Server cần …_KEY", or the text step works while the video step (queued by the text worker) blocks. Compare the `process_started` lines of the API and every worker: `env_file` and the key fingerprints must match. Restart the process that differs. |

Logs are JSON lines on stderr, one per event: `workflow_run_started`, `workflow_step_*`, `credit_reserved`, `credit_refunded`, `job_claimed`, `provider_request_started` / `completed` / `failed`, `job_completed`, `job_failed`. Each carries the workspace, workflow, run, step and job IDs, the provider and the model. For a failure it adds the `error` code, category, `retryable` flag and HTTP status, plus `provider_detail`: the provider's own message, at most 200 characters. That detail is never stored in the step or returned by the API. Prompts appear only as character counts, and secrets are redacted. Set `REELFORGE_LOG_FORMAT=text` for `event key=value` lines instead.

## Timeouts

| Call | Limit |
| --- | --- |
| Text provider request (OpenAI, Anthropic, Gemini) | connect 10 s; read, write and pool 120 s |
| Text job | at most 3 attempts, backoff 5 s × 2ⁿ; worker lease 300 s |
| Video provider API call (fal, Runware, Replicate, Runway, Dola) | 10 s for connect, read, write and pool |
| Video polling | every 10 s; 3 failed polls in a row end the job; at most `VIDEO_JOB_MAX_AGE_SECONDS` (default 21600 s), Dola `DOLA_MAX_JOB_AGE_SECONDS` (default 7200 s); lease 300 s |
| Video download | connect 30 s; each read 120 s; at most 100 MB |
| YouTube upload | connect 30 s; each read 120 s |
| Video smoke test | `--timeout` 900 s, polling every `--poll` 10 s |
