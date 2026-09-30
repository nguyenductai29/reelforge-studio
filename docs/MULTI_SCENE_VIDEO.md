# Multi-Scene Video

Phase 5 lets one Video step make one clip per scene. Each clip is its own durable job with its own credit reservation and its own reconciliation. Clips are not concatenated: joining clips, adding voice or subtitles, and rendering a final video are later phases.

## Modes

The mode is chosen in this order (`VideoNodeHandler.operations` in `app/workflow/nodes/video.py`):

| Inputs | Mode | Clips |
| --- | --- | --- |
| Prompt override set (or `prompt` sent when starting the run, for older API clients) | single | one clip of that prompt, even if scenes are connected |
| Scenes connected and the override empty | scenes | one clip per scene, from its `visual_prompt`, else its `text`; never joined |
| Only text connected, or nothing | single | one clip of the connected text, else the project topic |

Scenes win over connected text, so `Scene Splitter → Video` makes one clip per scene even when the script is also connected. Before this phase, the scenes were joined into one prompt (`Shot 1: … Shot 2: …`). That behavior is gone.

A step makes at most 20 clips. The model, aspect ratio, clip length, resolution and audio are the same for every clip of the step. Every scene's request is validated before any credit is reserved.

Several Video steps may now exist in one workflow. The previous "one video step per workflow" limit (`unsupported_graph`) came from the per-run reservation and was removed with it.

## Jobs

| Mode | Job logical key | Worker path |
| --- | --- | --- |
| scenes | `video:<step_id>:scene:<n>` (one per scene) | shared child-job engine, `app/media_jobs.py` (`VIDEO_KIND` in `app/video_worker.py`) |
| single | `video:<step_id>:single` | the step-level path that single clips always used |

`python -m app.video_worker` runs both; no new process is needed. Each scene job moves through queued → submitting → running → succeeded, failed or needs_attention, recorded in `step.output["jobs"][<job_id>]`. Scene jobs use `VIDEO_JOB_MAX_AGE_SECONDS`; for Dola, `DOLA_MAX_JOB_AGE_SECONDS` applies when it is shorter.

## Credits

`VIDEO_CREDITS_PER_CLIP` (default 10) is charged per clip. When the step is queued, the API locks the credit account and checks that the balance covers every clip. It then reserves each one separately:

| Operation | Scene clip | Single clip | Legacy (queued before this phase) |
| --- | --- | --- | --- |
| Reserve | `video-reserve:<step_id>:scene:<n>` | `video-reserve:<step_id>:single` | `reserve:<run_id>` |
| Usage | `video:<step_id>:scene:<n>` | `video:<step_id>:single` | `video:<step_id>` |
| Refund | `video-refund:<step_id>:scene:<n>` | `video-refund:<step_id>:single` | `refund:<run_id>` |

New operations never use `reserve:<run_id>`. Each job carries its three references in its payload. The worker and reconciliation use those references and check that they name the job's step and operation. A payload without references is a legacy job and keeps the legacy references, so video jobs, incidents and decisions from before this phase still settle and reconcile unchanged.

Readiness shows the per-clip price. When scenes will drive the step, it uses code `per_scene` ("10 credits per scene are held when the step starts"), because the scene count is known only once the upstream steps have run. If the balance cannot cover every scene when the step starts, nothing is reserved and the step reports `insufficient_credits`.

## Outcomes and partial failure

These are the same rules as images (`docs/IMAGE_GENERATION.md`) and single clips:

| Outcome of one clip | Credits | Job status |
| --- | --- | --- |
| Stored | charged | `succeeded` |
| Definite rejection before acceptance: invalid request, authentication, billing, not found, rate limit, missing key or configuration, full media storage, expiry while queued | refunded automatically | `failed` |
| Uncertain: lost or interrupted submit, failure after acceptance, polling errors, expiry after submission, invalid MP4 | held | `needs_attention` |

- Stored clips are always kept.
- The step settles when every job has finished:
  - all clips stored → `completed`, and downstream steps (Review) run;
  - any clip uncertain → `needs_attention`;
  - otherwise → `failed`.
- Nothing is regenerated automatically.

## Reconciliation

Each uncertain scene is its own item in **Admin → Reconciliation**. An item shows:

- the scene index and operation (`scene:3`);
- the remote request ID, reserved credits, provider and model;
- the run, step and job IDs;
- the error category and whether the submission succeeded.

An admin decides each item on its own, with `POST /api/admin/reconciliation/jobs/{job_id}/confirm-charge|refund` (migration 0012 stores one decision per job). A scene can be decided while the other scenes of the step are still running.

The step stays `needs_attention` while any scene waits for a decision or was confirmed as charged. It becomes `failed` once every uncertain scene was refunded. The older `/api/admin/reconciliation/{step_id}/…` routes still decide steps that have one job (single clips and legacy incidents), and they answer 409 for a step with several jobs. See `docs/CREDIT_RECONCILIATION.md`.

## Output

`video_assets` lists every stored clip in scene order:

```json
{"id": "…", "asset_id": "…", "filename": "video-1a2b3c4d.mp4", "content_type": "video/mp4",
 "provider": "fal", "model": "fal-ai/veo3.1/fast", "scene_index": 2, "duration": 8.0}
```

`duration` is read from the MP4 (`mvhd`), or else it is the requested length. A single clip has `scene_index: null` and still sets `asset_id` and `filename` on the step output, as before. The step output also has `mode`, `expected`, `operations`, `asset_ids`, `aspect_ratio` and `duration`.

## Review and publishing

Review needed only a small change: it already accepted several assets.

- A Review step after a multi-scene Video step waits with every clip's asset ID.
- Approving it approves the run. Approval is allowed when any completed step produced media.
- Publishing (YouTube) accepts any MP4 of a completed video step in an approved run. The publish dialog offers the first scene's clip, because there is no joined video until the render phase.

## Interface

- **Canvas node:** the clip count, or progress (`Generating 2/3`) while jobs run.
- **Inspector:** every clip with its scene label, a player and a download link, plus a per-scene status list while any clip is unfinished or failed. A single clip shows one large player, as before.

## Retry limitations

- A retry creates a new run from the original graph snapshot and generates **every** scene again with the current tool and price. Clips that succeeded in the failed run are not reused, and their credits are not credited against the new run.
- Only a single-clip request is frozen on retry. The same prompt, model and price are reused, but only for the Video node that queued it (older payloads without a node ID apply to the workflow's one video node).
- Retry is refused while any clip waits for a decision or was confirmed as charged. It is also refused when the run has stored clips and a clip was refunded by reconciliation, because the new run would regenerate clips that were already paid for. Start a new run deliberately instead.
- There is no per-scene retry, cancellation or resubmission of a remote job.

## Logs

Scene jobs log `job_claimed`, `provider_request_started`, `provider_request_completed`, `provider_request_failed`, `job_failed`, `credit_refunded`, `job_completed` and `media_step_settled`. Each event carries `workspace_id`, `workflow_id`, `run_id`, `step_id`, `job_id`, `scene_index`, `job_operation`, `provider` and `model`. Keys, signed URLs, submission URLs and prompts are never logged.

## Tests

`tests/test_multi_scene_video.py`:

- Mode precedence: override → single; scenes with an empty override → scenes; only text → single. A run prompt counts as an override. The retry freeze applies only to the matching single-clip node.
- One clip per scene through the real API and worker:
  - per-scene keys and references;
  - `video_assets` fields;
  - usage, logs, Review and approval.
- Partial failure: one clip stored, one refunded, one reconciled; retry refused.
- Failures after acceptance and invalid files, decided per scene.
- All rejected → refunded, and retry regenerates every scene.
- Single clip with `video:<step>:single` references.
- Legacy payloads with `reserve:<run>` / `refund:<run>` / `video:<step>` still refund, settle and reconcile.
- Idea → AI Writer → Scene Splitter (3) → Image (3 images) and Video (3 clips) → Review, approved.

Existing video tests were updated for the new references and for scene mode replacing the joined prompt.
