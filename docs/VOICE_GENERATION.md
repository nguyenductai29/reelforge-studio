# Voice Generation (Text-to-Speech)

Phase 6 makes the **Voice** node executable. A Voice step reads a script aloud as one narration, or reads each scene as its own narration segment. Every narration is its own durable job with its own credit reservation. The audio is stored as a private workspace asset.

## Provider: Google Gemini TTS

| | |
| --- | --- |
| Adapter | `app/providers/voice/gemini.py` (`GeminiVoiceProvider`) |
| Models | `gemini-2.5-flash-preview-tts`, `gemini-2.5-pro-preview-tts` |
| Request | `POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent` with `responseModalities: ["AUDIO"]` and a prebuilt voice; key in the `x-goog-api-key` header |
| Response | base64 PCM (16-bit, mono, 24 kHz) in the same HTTP response, wrapped by ReelForge into a WAV file |
| Credentials | `GEMINI_API_KEY`, the same key Gemini text uses |

Why Gemini:

- The repository already calls Gemini for text.
- The operator already verified its key live.
- It needs no new dependency and no new secret.
- It returns the audio in the response, which suits short scene narrations.

OpenAI TTS and ElevenLabs would each need a new key that this deployment does not have.

Gemini has no speed, pitch or output-format parameter, so the inspector does not offer them. Gemini detects the language from the text (Vietnamese, English and Japanese included), so there is no language setting either. Delivery is directed in natural language: the **Delivery** setting adds a short instruction before the text, such as "Say calmly:".

The layer in `app/providers/voice/` is provider-neutral:

- `base.py` defines `VoiceGenerationProvider.generate(VoiceRequest) → VoiceResult`. The result holds the audio bytes, content type, duration, remote request ID and usage.
- `base.py` also defines `VoiceModel` (voices, styles, formats, maximum text length) and `VoiceProviderError`, which uses the shared error categories.
- `__init__.py` holds the catalog (`VOICE_PROVIDERS`), `create_voice_provider` and the price.

To add a provider, add one adapter and one catalog entry. No workflow code sees a vendor response.

## Settings (inspector)

| Field | Values | Notes |
| --- | --- | --- |
| Model | an enabled AI tool with task **Voice** | The Models page has a "Google · Gemini 2.5 Flash TTS" preset |
| Voice | the 30 Gemini prebuilt voices (default `Kore`) | Readiness checks the voice against the chosen model |
| Delivery | neutral, calm, cheerful, energetic, serious, slow | Natural-language direction |
| Text override | at most 5,000 characters | Read instead of any connected input |

## Inputs and modes

The text to read is chosen in this order:

1. **Text override** → one narration (operation `single`).
2. **Connected script/text** (AI Writer, Summarize…) → one narration of it.
3. **Connected scenes** → one narration per scene (operations `scene:<n>`). Each narration reads the scene's `text`, never its `visual_prompt`. Scenes are never joined, and `scene_index` is kept.

The project topic is never read aloud: a Voice step without text or scenes is blocked.

Text is never cut. Whitespace is collapsed, and text longer than 5,000 characters blocks the step before any credit is held. In that case, connect a Scene Splitter to read the script scene by scene. A step makes at most 20 narrations.

## Jobs, worker and storage

- The job kind is `voice.generate`, with logical keys `voice:<step_id>:single` or `voice:<step_id>:scene:<n>`.
- Run `python -m app.voice_worker` (`--once` for one job). In production it runs as the systemd unit `reelforge-voice-worker`.
- The lifecycle is shared with images and scene clips (`app/media_jobs.py`). A provider that answers with the file itself uses the synchronous path: generate → validate → store. Nothing is polled or downloaded, and no provider URL is stored.
- `VOICE_JOB_MAX_AGE_SECONDS` (default 1800) ends a job that waited too long.
- **File checks** (`app/audio_files.py`) look at the file's bytes, never its extension:
  - A WAV must be PCM with consistent RIFF, `fmt ` and `data` chunks; its duration is computed from them.
  - An MP3 must start with an ID3 tag or an MPEG audio frame whose next frame follows.
  - HTML or JSON error bodies and truncated files are rejected.
  - Files over 50 MB or 30 minutes are rejected.
- **Assets** keep their lineage: workspace, project, run, step, provider (`gemini`), model, bytes and content type (`audio/wav`).
- **Output** `audio_assets`:

```json
{"id": "…", "asset_id": "…", "filename": "voice-1a2b3c4d.wav", "content_type": "audio/wav",
 "provider": "gemini", "model": "gemini-2.5-flash-preview-tts", "scene_index": 2, "duration": 3.42}
```

`scene_index` is `null` for a single narration. The step output also has `mode`, `expected`, `operations`, `jobs` (public facts only) and `asset_ids`.

## Credits

`VOICE_CREDITS_PER_GENERATION` (default 1, allowed 1–100000) is the price of one narration. It is a flat price, not the provider's cost.

The API checks, under a lock, that the balance covers every narration of the step, then reserves each one:

| Event | Reference |
| --- | --- |
| Reserve (when queued) | `voice-reserve:<step_id>:<operation>` |
| Usage (when stored) | `voice:<step_id>:<operation>` |
| Refund | `voice-refund:<step_id>:<operation>` |

| Outcome of one narration | Credits | Job status |
| --- | --- | --- |
| Stored | charged | `succeeded` |
| Rejected before the provider accepted it: HTTP 400/401/403/402/404/429, missing key, full storage, expiry while queued | refunded automatically | `failed` |
| Uncertain: timeout or lost connection after sending, provider 5xx, blocked or empty answer after HTTP 200, invalid audio, interrupted worker | held | `needs_attention`; each narration is decided on its own in **Admin → Reconciliation** |

Gemini returns a `responseId`, which is recorded as the remote request ID when present.

Successful narrations are kept when others fail. The step completes, and downstream steps run, only when every narration is stored. Nothing is generated again automatically.

## Interface

- **Node:** a single narration plays on the node. Scene narrations show their count, and `Generating x/y` while running.
- **Inspector:** one player per narration with its scene label, duration and download link, plus per-job statuses.
- **Readiness** checks:
  - the model is enabled for task Voice;
  - `GEMINI_API_KEY` is set;
  - the model is supported;
  - the voice and delivery are valid for the model;
  - a script or scenes are connected;
  - credits: per scene (`per_scene`) or for one narration.

## Manual verification (paid)

```bash
python -m app.provider_check --only voice                 # no request: key and model
python -m app.smoke_test voice --live                     # one short sentence, voice Kore
python -m app.smoke_test voice --live --voice Puck --text "Xin chào các bạn"
```

The smoke test prints the model, request ID, duration, size and latency, and saves the WAV under `instance/smoke-tests/`. `REELFORGE_SMOKE_VOICE_PROVIDER` and `REELFORGE_SMOKE_VOICE_MODEL` choose defaults. The test suite never calls Gemini.

## Limitations

- The Gemini TTS model names are preview names. If Google renames them, add the new names to `VOICE_MODELS` in `app/providers/voice/gemini.py`; until then the request fails as `not_found` and is refunded.
- There is no speed, pitch or language parameter, no voice cloning, and no custom voices.
- Output is WAV (about 48 KB per second), which is larger than MP3.
- The price is flat per narration, not per character.
