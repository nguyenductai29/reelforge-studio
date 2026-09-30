# Rendering (FFmpeg)

Phase 8 makes the **Render** node executable. A Render step joins the scene clips, adds the narration and burns in the subtitles, producing one final H.264/AAC MP4. The MP4 is stored as a ReelForge asset that Review previews and publishing prefers.

```text
Idea → AI Writer → Scene Splitter ─┬→ Video ───────────────┐
                                   ├→ Voice ─┬─────────────┤
                                   └→ Subtitle ←┘ (audio)  ├→ Render → Review
                                          └────────────────┘
```

## Requirements

System FFmpeg is required; the repository never bundles it.

- **Ubuntu:** `sudo apt install -y ffmpeg fonts-noto-core fonts-noto-cjk`.
- **Windows development:** install FFmpeg (for example with `winget install Gyan.FFmpeg`) and put `ffmpeg.exe` and `ffprobe.exe` on `PATH`, or set `RENDER_FFMPEG_PATH` and `RENDER_FFPROBE_PATH` to their full paths.
- **Check:** `python -m app.render_worker --check` prints the tools and the subtitle font, and exits 1 when something is missing.

Every process that advances runs (the API and each worker) checks `ffmpeg` and `ffprobe` before it queues a render. Install FFmpeg where they run, or set the two paths in the shared runtime file.

| Variable | Default | Meaning |
| --- | --- | --- |
| `RENDER_FFMPEG_PATH`, `RENDER_FFPROBE_PATH` | from `PATH` | Executables to use |
| `RENDER_SUBTITLE_FONT` | `Noto Sans` | Font family for burned-in subtitles (letters, digits, spaces, `-` and `_` only) |
| `RENDER_TIMEOUT_SECONDS` | 1800 (60–21600) | Longest render before FFmpeg is stopped |
| `RENDER_CREDITS_PER_JOB` | 0 (0–100000) | Price of one render |

## Inputs and checks

| Port | Accepts | Notes |
| --- | --- | --- |
| `media` (required) | `video_assets` | The clips, in the order the Video step lists them (scene order). Images are refused: slideshows are not supported yet |
| `audio` | `audio_assets` | Voice narration |
| `subtitle` | `subtitle_asset` | Subtitle output: cues, scene spans and burn-in style |

Before a job is queued, the step checks each of the following. If one fails, the step is blocked with a clear reason and nothing is queued:

- every clip, narration and subtitle is an asset of this workspace, of the right type, with its file on disk;
- FFmpeg and ffprobe exist;
- with subtitles, the font exists (checked with `fc-list` where fontconfig is installed);
- the workspace has storage left;
- the balance covers the price, when a price is set.

The job payload freezes the asset IDs, their order and the subtitle cues. Readiness reports `ffmpeg_missing` or `font_unavailable`.

## Worker

Run `python -m app.render_worker` (`--once` for one job). In production it runs as the systemd unit `reelforge-render-worker`.

Each job (`render.generate`, logical key `render:<step_id>:final`) runs outside any database transaction:

1. `ffprobe` each clip: duration, audio stream and frame size.
2. Write the re-timed subtitles to `subtitles.srt` in a private folder, `<media>/.render-tmp/<job_id>`.
3. Run one FFmpeg command:
   - an argument list, never a shell;
   - with the folder as its working directory, so the subtitle filter uses a relative name and no path is quoted inside the filtergraph;
   - with timeout `RENDER_TIMEOUT_SECONDS`.
4. Check that the output is a valid MP4 (at most 500 MB) and probe it.
5. Move it into the workspace media, record the asset and finish the step. Review then waits for approval.
6. Always delete the folder.

The job's lease is longer than the timeout, so a second worker never renders the same job. A job interrupted by a crash is rendered again when its lease expires, at most 3 times. A finished job is never rendered again.

## What the render does

- **Video:** each clip is scaled and padded (letterboxed) to the first clip's frame size, capped at 1920 pixels on the long side and rounded to even numbers. It runs at 30 fps and keeps its own length. Clips are joined in order. Output settings: H.264 (`libx264`, `veryfast`, CRF 23, `yuv420p`), AAC 160 kbit/s at 48 kHz stereo, `+faststart`.
- **Audio** (voice takes precedence):
  - with narration, the clips' own audio is muted;
  - one narration per scene is placed at the start of its scene's clip, then padded with silence or cut to the clip's length, so the video keeps its length; a scene without narration is silent;
  - one narration for the whole video (or narration that does not match the clips' scenes) is joined in order and padded or cut to the video's length;
  - without narration, each clip keeps its own audio, and a clip without audio is silent.
- **Subtitles** are re-timed onto this timeline:
  - Cues that belong to a scene are placed inside that scene's clip.
  - Cues timed by narration keep their pace and are cut where the clip (and the narration) ends.
  - Cues timed by scene estimates or clips are stretched to the clip.
  - Cues without a scene keep their times.
- **Burn-in:** the FFmpeg `subtitles` filter (libass) draws the text with `force_style`:
  - font `RENDER_SUBTITLE_FONT`;
  - size small 14, medium 18 or large 24 (libass units on a 288-line canvas);
  - preset: white with a black outline, text on a dark box, or bold yellow;
  - bottom-centred, with a margin of 24.

  Text is UTF-8, so Vietnamese and Japanese render when the font has their glyphs. libass falls back to other installed fonts, so install Noto CJK for Japanese. No font file is committed.

## Output, Review and publishing

- **Asset:** `render-<id>.mp4`, with provider `ffmpeg`, model `local`, and its workspace, project, run and step lineage.
- **Output** `video_assets` (port `rendered_video`) holds one entry:

```json
{"id": "…", "asset_id": "…", "filename": "render-1a2b3c4d.mp4", "content_type": "video/mp4",
 "provider": "ffmpeg", "model": "local", "scene_index": null, "duration": 18.0,
 "width": 720, "height": 1280, "final": true}
```

- **Review:** after Render, Review previews the final video only. Workflows whose Review follows a Video step still review the clips.
- **Publishing** prefers the final render:
  - The publish dialog and the run bar pick a completed Render step's video before any clip.
  - The API accepts MP4s from completed `render` steps as well as `video` steps.

## Credits and failures

Rendering is free by default. With `RENDER_CREDITS_PER_JOB` above 0, one render reserves `render-reserve:<step>:final`, charges `render:<step>:final` when stored, and refunds `render-refund:<step>:final` on failure.

Rendering calls no paid provider, so a failure is never uncertain: nothing needs reconciliation, and a reserved price is always refunded. Failures are reported with a code and category, and the FFmpeg message (last lines, file paths removed) appears in the inspector:

| Code | Cause |
| --- | --- |
| `ffmpeg_missing` | FFmpeg or ffprobe is missing or cannot start |
| `font_unavailable` | The subtitle font is not installed |
| `input_missing` | An input file disappeared after the job was queued |
| `render_failed` | FFmpeg or ffprobe returned an error |
| `render_timeout` | FFmpeg ran longer than `RENDER_TIMEOUT_SECONDS` |
| `invalid_output` | FFmpeg produced no valid MP4 |
| `storage_limit_exceeded` | The workspace's storage filled up |
| `attempts_exhausted` | The worker was interrupted three times |

A failed render fails the run. Retrying the run starts a new run, which generates the paid upstream steps again; see the limitations.

## Logs

- `render_started`, `render_completed` and `render_failed` carry `workspace_id`, `workflow_id`, `run_id`, `step_id`, `job_id`, `clip_count`, `audio_count` and `subtitle`.
- `render_completed` adds `duration_ms`, `output_bytes` and `video_seconds`.
- `render_failed` adds `error_code` and `error_category`.
- The FFmpeg command and file paths are not logged.

## Interface

- **Node:** the final video's player and length.
- **Inspector:**
  - input clips, narration and subtitles;
  - the audio policy;
  - duration and resolution;
  - a player and a download link;
  - the FFmpeg error, if any.

## Tests

- `tests/test_render.py`:
  - concat order;
  - voice precedence and scene alignment;
  - one narration for the whole video;
  - clip audio and silence;
  - the subtitle filter and style quoting;
  - frame size;
  - subtitle re-timing;
  - running without a shell in the job folder;
  - path-free error messages, timeouts and a missing FFmpeg;
  - probing, tools, fonts and settings;
  - what the Render step checks and freezes;
  - a real FFmpeg render of synthetic media, which runs only where FFmpeg is installed.
- `tests/test_render_worker.py`, with a fake FFmpeg over HTTP:
  - Idea → AI Writer → Scene Splitter → Video + Voice + Subtitle → Render → Review;
  - the final asset and its lineage;
  - the burned subtitle timing;
  - temporary files removed;
  - Review and publishing on the final render;
  - no second render;
  - logs;
  - a failure with refund;
  - a missing FFmpeg, a missing input and a timeout.

## Manual verification

On a machine with FFmpeg:

```bash
python -m app.render_worker --check
python -m unittest tests.test_render -v          # includes the real FFmpeg test when FFmpeg is installed
```

Then run the full workflow in the editor (see `docs/LIVE_PROVIDER_SMOKE_TEST.md`) and check:

- the final MP4's length equals the sum of the clips;
- narration starts with each scene;
- subtitles are readable in Vietnamese and Japanese.

## Limitations

- **No image slideshow or music track.** Only video clips are joined.
- **Narration and video length:** narration longer than its clip is cut; there is no time-stretching, and the video is never lengthened.
- **Frame size:** clips with a different aspect ratio than the first are letterboxed.
- **Subtitle timing** follows narration, clip or scene lengths, not speech recognition.
- **Font check:** the font is checked only where `fc-list` exists; elsewhere libass picks a fallback font.
- **Resources:** rendering uses the server's CPU and disk; keep one render worker on a small server. A folder left in `.render-tmp` by a killed worker can be deleted when no render is running.
- **Retry:** there is no per-step re-render. Retrying a failed run generates every paid step again (clips, narration), as for any retry.
