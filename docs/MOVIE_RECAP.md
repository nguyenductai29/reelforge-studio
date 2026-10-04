# Movie Recap / Review

> **Use only content you are authorized to use.** ReelForge works only from files your workspace uploaded. It does not download from streaming services, bypass DRM or any other protection, or remove watermarks.

The **Movie Recap / Review** template (`movie_recap`) turns an uploaded video into a narrated recap. Each recap scene is matched with the moment of the source video it talks about.

```text
Uploaded Media Source ─→ Transcript ─→ Story Analysis ─→ Recap Script ─┬→ Voice ──────────────┐
        │                     │                               │          └→ Subtitle ←┘(audio)   │
        │                     └──────────→ Match Source Scenes ←┘ (scenes)                        │
        └─────────(video)───────────────→ Match Source Scenes → Extract Source Clips → Render ←──┘
                                                                     Render → Review → Publish
                                          Recap Script → Metadata → Publish
```

## Steps

**Transcript** (`transcribe`)
- Timestamped speech-to-text of the uploaded video, as described in `docs/CONTENT_SOURCES.md`.
- If you already have subtitles, upload the SRT or VTT file as the source instead. They pass through for free, with their cue times.

**Story Analysis** (`story_analysis`)
- One JSON text generation over the transcript. It sees timestamps as `[mm:ss]` lines.
- Returns `title`, `summary`, `characters[{name, role, description}]`, `plot_points[]`, `acts[{name, summary}]`, `important_moments[{description, quote, start, end}]` and `themes[]`.
- Every field is bounded. A reply that is not JSON still gives a summary.

**Recap Script** (`recap_script`)
- One JSON text generation. It outputs `script` (text), `scenes` and `title`.
- Settings:
  - language and tone;
  - target length, 15–900 s;
  - spoiler level: none, light or full;
  - platform;
  - style: summary, storytelling, review or explainer;
  - number of scenes, 2–20.
- Each scene has:
  - `text`: the narration;
  - `source_quote`: one line of original dialogue, copied from the source;
  - `moment`: what is on screen.

**Match Source Scenes** (`match_scenes`)
- Local and free; no model is called. The algorithm is in `app/scene_matching.py`.
- For each scene, it compares the quote (else the moment, else the narration) with every window of consecutive transcript segments. The score blends:
  - IDF-weighted shared words;
  - character-trigram overlap.
- Accents and case are ignored, and very common words do not count.
- A scene whose best score is below `min_confidence` (default 0.2) gets the moment at the same relative position in the video (`reason: position_fallback`, confidence 0). Every scene therefore gets a clip a person can review.
- Clips are padded (default 0.3 s) and kept between `min_seconds` and `max_seconds` (default 3–12 s). They are stretched to the narration's estimated length (2.5 words per second) so the voice-over is not cut short.
- Output `source_clips`: `[{scene_index, source_asset_id, start, end, confidence, reason, matched_words, text}]`.

**Extract Source Clips** (`extract_clips`)
- Local and free. The step checks every source asset (it must belong to the workspace and be MP4 or WebM, with its file present), FFmpeg and storage. It then queues one `clips.extract` job (`render:<step>:clips`) for the render worker.
- For each clip, the worker:
  - tries a **stream copy** first, for MP4 sources: fast, but it starts on a keyframe;
  - checks the result is a valid MP4 with video;
  - otherwise **re-encodes** to H.264/AAC, which is frame-accurate. Cut mode `reencode` always re-encodes.
- Only the first video and audio streams are kept.
- Each clip is stored as an MP4 asset of the run, with `assets.source_asset_id` set to the source video (migration 0014). The output lists them in scene order with `cut: copy|reencode`.

**Render**
- Joins the extracted clips like generated clips, one per scene.
- Per-scene narration from Voice starts with its scene's clip. The clips' own audio is muted while narration plays (see `docs/RENDERING.md`).
- The source video itself is never offered for publishing: only the final render is.

## Setup

- Workers:
  - `app.source_worker` (Transcript, needs FFmpeg);
  - `app.text_worker`;
  - `app.voice_worker`;
  - `app.render_worker` (clip extraction and render).
- AI models: one Text, one Voice and one Transcription model (OpenAI · Whisper).
- Credits with the defaults, for a recap of 6 scenes:
  - 2 for the transcription;
  - 1 each for Story Analysis, Recap Script and Metadata;
  - 1 per narration (6);
  - matching, extraction, subtitles and render are free.

  Total: 2 + 3 + 6 = 11.

## Notes and limits

- Matching works on dialogue. Scenes with no speech in the source fall back to their relative position: review the clips before approving.
- A source with no timestamps cannot be matched. Match Source Scenes blocks with `missing_timestamps`. Use Transcript, or upload an SRT/VTT file.
- Transcription accepts media up to `TRANSCRIPTION_MAX_SECONDS` (default 3 hours). Uploads are limited to 100 MB, so long films need a compressed copy.
- Clips are 0.2 to 120 s each, at most 20 per step.
