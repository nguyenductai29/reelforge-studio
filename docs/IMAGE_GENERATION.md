# AI Image Generation

Phase 4 makes the **Image** node executable. An Image step generates one image per scene, or a chosen number of images from one prompt, through a provider-neutral image layer. Each image is its own durable job with its own credit reservation, and the files are copied into ReelForge's private media storage.

## Provider: Runway `gen4_image`

| | |
| --- | --- |
| Adapter | `app/providers/image/runway.py` (`RunwayImageProvider`) |
| Model | `gen4_image` |
| Request | `POST https://api.dev.runwayml.com/v1/text_to_image` with `model`, `promptText`, `ratio` and an optional `seed` (API version `2024-11-06`) |
| Status and result | `GET /v1/tasks/{id}`; the finished task lists output URLs |
| Credentials | `RUNWAYML_API_SECRET`, `RUNWAY_OUTPUT_HOSTS` (the same ones Runway video uses) |

Why Runway:

- It reuses the Runway client the video provider already uses, including authentication, API versioning, error mapping and the output host allowlist (`RUNWAY_OUTPUT_HOSTS`) that protects downloads against SSRF. No new dependency or credential is needed.
- The operator already verified the Runway key live for video before this phase.
- Runway's task API returns a task ID and is polled later, so it fits the durable submit, poll, download flow that video jobs already use. A lost response can be reconciled by task ID.
- It supports the three aspect ratios the studio uses, a seed, and two resolutions.

`gen4_image_turbo` is not offered because it requires reference images, which this phase does not accept.

The layer in `app/providers/image/` is provider-neutral:

- `base.py` defines `ImageGenerationProvider`: `capabilities`, `validate`, `submit`, `status`, `result`, `validate_media_url` and `generate`. It also defines the request, submission, status and result types, and `ImageProviderError`, which uses the shared error categories in `app/providers/errors.py`.
- `__init__.py` holds the provider catalog (`IMAGE_PROVIDERS`), `create_image_provider` and the credit price.

To add a provider, add one adapter and one catalog entry.

## Settings (inspector)

The inspector's fields come from the handler's schema (`config_fields` in `app/workflow/nodes/image.py`, served by `GET /api/workflow-node-types`), so the API validates exactly what the inspector shows.

| Field | Values | Notes |
| --- | --- | --- |
| Model | an enabled AI tool with task **Image** | Code `unsupported_model` for any other tool |
| Aspect ratio | `auto`, `1:1`, `16:9`, `9:16` | `auto` follows the workspace orientation: vertical 9:16, horizontal 16:9, square 1:1. Checked against the model's capabilities |
| Number of images | 1–4 (default 1) | Prompt mode only; scene mode makes one image per scene |
| Quality | `standard` (720p), `high` (1080p) | Standard is 720×720, 1280×720 or 720×1280. High is 1080×1080, 1920×1080 or 1080×1920 |
| Prompt override | text, at most 1,000 characters | Replaces every other input |
| Seed (advanced) | 0–4294967295 | Only for models that support a seed |

Runway has no negative prompt or style parameter, so the inspector offers neither. A negative prompt sent through the API is rejected (`invalid_request`). Style comes from the scene splitter's **visual style**, which is already part of each scene's `visual_prompt`.

## Inputs and modes

The prompt is chosen in this order:

1. **Prompt override** → `count` images of that prompt (operations `image:1` … `image:n`).
2. **Connected prompt text** (AI Writer, Summarize, an Idea's brief…) → `count` images.
3. **Connected scenes** → one image per scene (operations `scene:<n>`). Each scene uses its `visual_prompt`, or its `text` when there is none. Scene prompts are never joined. The scene's `index` is kept as `scene_index`.
4. **Project topic** (or title) → `count` images.

A step makes at most 20 images; the scene splitter never produces more. Every prompt is cut to 1,000 characters at a word boundary. All requests are validated before any credit is reserved.

## Jobs and the worker

- Job kind `image.generate`, one job per image, with logical key `image:<step_id>:<operation>` (for example `image:<step_id>:scene:2`). Keys are unique, so a step can never queue the same image twice.
- Run `python -m app.image_worker` continuously next to the API; `--once` processes one due job. In production it is its own systemd unit, `reelforge-image-worker` (see `docs/home-server-deployment.md`). `deploy.sh` restarts it when the unit is enabled.
- The job lifecycle is shared with multi-scene video (`app/media_jobs.py`): queued → submitting → running → succeeded, failed or needs_attention. Each job's state is kept in `step.output["jobs"][<job_id>]`. The API returns only its operation, scene index, status, stored assets, error category and reconciliation summary; submission handles and provider progress stay private.
- `IMAGE_JOB_MAX_AGE_SECONDS` (default 3600, allowed 60–86400) ends a job that waits too long.

## Credits

`IMAGE_CREDITS_PER_GENERATION` (default 2, allowed 1–100000) is the price of one image. When the step is queued, the API locks the credit account and checks that the balance covers every image of the step, then reserves each image separately:

| Operation | Reference | Balance |
| --- | --- | --- |
| Reserve (when queued) | `image-reserve:<step_id>:<operation>` | minus the price |
| Usage (when the file is stored) | `image:<step_id>:<operation>` | none; one usage event |
| Refund (definite rejection) | `image-refund:<step_id>:<operation>` | plus the price |

A step never holds part of its cost: if the balance is short, nothing is reserved and the run start returns 402 (`insufficient_credits`).

| Outcome of one image | Credits | Job status |
| --- | --- | --- |
| Stored | charged (usage event) | `succeeded` |
| Rejected before the provider accepted it: invalid request, authentication, billing, not found, rate limit, missing key or configuration, full media storage, expiry while still queued | refunded automatically | `failed` |
| Uncertain: lost or interrupted submit, provider failure after acceptance, polling errors, expiry after submission, invalid or oversized file | held | `needs_attention`; an admin decides this image alone in **Admin → Reconciliation** (see `docs/CREDIT_RECONCILIATION.md`) |

## Partial failure

- Successful images are always stored and kept, even when other images of the step fail.
- The step settles only when every job has finished:
  - all succeeded → `completed`, and downstream steps run;
  - any image uncertain → `needs_attention`;
  - otherwise → `failed`.
- The output `image_assets` always lists the stored images, ordered by scene.
- Nothing is regenerated automatically. A retry of a failed run creates a new run that generates every image again and reserves new credits. A run is not retried while an image waits for a decision or was confirmed as charged, nor when it has stored images and an image was refunded by reconciliation.

## File safety

Each output URL is checked before download:

- It must be `https` on a host listed in `RUNWAY_OUTPUT_HOSTS`.
- It must have no user info or fragment.
- The path must end with an image extension.

The worker then downloads the file itself (`app/image_files.py`):

- Redirects are refused, the `Content-Type` must be PNG, JPEG, WEBP or `application/octet-stream`, and the file may be at most 20 MB.
- The file's bytes are checked: PNG needs IHDR and IEND, JPEG needs an SOF marker and EOI, and WEBP needs a consistent RIFF size.
- SVG and every other format are rejected. Dimensions must be 1–20,000 pixels.
- The file is written to `<asset_id>.part` and renamed only after these checks. The workspace media quota is checked before the asset is recorded.

Signed output URLs and keys are never logged, stored or returned.

## Output

`image_assets` is the node's output port. It is the existing `image_assets` data type, so the image can feed Review or any other input that accepts it. Each entry looks like this:

```json
{"id": "…", "asset_id": "…", "filename": "image-1a2b3c4d.png", "content_type": "image/png",
 "provider": "runway", "model": "gen4_image", "scene_index": 2, "width": 1280, "height": 720}
```

`scene_index` is `null` in prompt mode. Each asset row keeps its lineage (project, run, step, provider, model). The step output also carries `mode`, `expected`, `operations` and `asset_ids`.

## Interface

- **Canvas node:** a grid of up to four previews and the image count. While jobs run, it shows progress (`Generating 2/3`).
- **Inspector:** every image with its scene or file number and a download link. While a job is unfinished or failed, it also shows a per-image status list (waiting, submitting, generating, done, failed and refunded, or needs reconciliation).
- **Readiness** checks, in order:
  - the step's settings;
  - that the selected model is enabled for task Image;
  - `RUNWAYML_API_SECRET` and a valid `RUNWAY_OUTPUT_HOSTS`;
  - that the model is supported and accepts the chosen aspect ratio, quality and seed;
  - credits: `count × price`, or the per-scene price with code `per_scene` when scenes will drive the step;
  - a required input.
- **Admin → Reconciliation** lists each uncertain image with its scene index.
- **Models page:** a "Runway · Gen-4 Image" preset.

## Smoke tests (manual, paid)

The test suite never calls Runway. To check a real key by hand:

```bash
python -m app.provider_check --only image          # no provider call: key, model, output hosts
python -m app.smoke_test image --live              # one small real image (1:1, standard)
python -m app.smoke_test image --live --aspect 16:9 --timeout 300
```

The smoke test prints the dimensions, size and latency, and saves the file under `instance/smoke-tests/`. `REELFORGE_SMOKE_IMAGE_PROVIDER` and `REELFORGE_SMOKE_IMAGE_MODEL` choose defaults. A live call also needs `REELFORGE_LIVE_TESTS=1` in the shell or `--live`.

## Tests

- `tests/test_image_provider.py`:
  - the request sent to Runway;
  - ratios per quality;
  - validation before any call;
  - status and result mapping;
  - unsafe output URLs;
  - HTTP error categories;
  - configuration issues;
  - PNG/JPEG/WEBP recognition and rejection of SVG, HTML, GIF and truncated or oversized files.
- `tests/test_image_nodes.py`:
  - the settings schema and invalid settings;
  - prompt mode and scene mode;
  - input precedence;
  - readiness, including per-scene and prompt-over-scenes estimates;
  - capability checks;
  - insufficient credits reserving nothing;
  - blocking without charging.
- `tests/test_image_worker.py`, over HTTP with the real worker:
  - one image per scene, then Review and approval;
  - partial failure with per-scene reconciliation;
  - invalid files and failed tasks holding credits;
  - definite rejections refunding and allowing retry.
- `tests/test_multi_scene_video.py`: Idea → AI Writer → Scene Splitter → Image (3) and Video (3) → Review, end to end.
