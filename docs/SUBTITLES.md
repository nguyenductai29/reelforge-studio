# Subtitles

Phase 7 makes the **Subtitle** node executable. A Subtitle step writes an SRT or WebVTT file from the scenes or the script. It is deterministic, local and free: it makes no provider call, queues no job and reserves no credits. The file is stored as a private workspace asset. The Render step can burn it into the final video (`docs/RENDERING.md`).

## Inputs

| Port | Accepts | Use |
| --- | --- | --- |
| `scenes` | Scene Splitter output | Preferred: each scene's text, index and estimated duration |
| `script` | text or brief | Used when no scene has text |
| `audio` | Voice output (`audio_assets`) | Real narration durations |
| `video` | Video output (`video_assets`) | Real clip durations |

`script` and `video` are the port names of the earlier placeholder, so saved workflows keep their edges. At least `scenes` or `script` must be connected.

## Timing

Timing comes from the best source available (`app/subtitles.py`):

1. **Narration** (`audio`):
   - With one narration per scene, each scene lasts as long as its narration.
   - With one narration for the whole script, that length is shared across the scenes in proportion to their estimated lengths.
2. **Clips** (`video`): each scene lasts as long as its clip.
3. **Scenes**: each scene's own `duration`, the Scene Splitter's estimate at 2.5 words per second.
4. **Estimate**: plain text without scenes is timed at 2.5 words per second (Japanese is counted by characters).

A scene without narration or a clip falls back to the next source. Scenes follow each other in scene order, with no gaps. A scene without text keeps its time but has no cue, so later subtitles stay aligned with the video.

Inside a scene:

- The text is wrapped into lines of at most **Max characters per line** (default 42). Words are kept whole unless one word is longer than a line, as in Japanese text without spaces, which is broken by characters.
- Lines are grouped into cues of at most **Max lines** (default 2).
- The scene's time is shared between its cues in proportion to their length.

There is no speech recognition or forced alignment.

Example for three scenes of 4, 3 and 5 seconds, where the second scene has no text:

```text
1
00:00:00,000 --> 00:00:04,000
Rừng đêm tĩnh lặng.

2
00:00:07,000 --> 00:00:12,000
Bình minh lên trên đồi.
```

## Text safety

- Line breaks and control characters become spaces, so a blank line cannot end an SRT cue early.
- `-->` becomes `→`, so text cannot start a new timing line.
- WebVTT escapes `&`, `<` and `>`.
- Text is UTF-8 without a BOM; Vietnamese and Japanese are kept as they are.
- For burn-in, Render also replaces `{`, `}`, `<`, `>` and `\` with look-alike characters, so libass cannot read them as override tags or markup.

## Settings

| Field | Values | Notes |
| --- | --- | --- |
| Format | SRT (default), WebVTT | Content type `application/x-subrip` or `text/vtt` |
| Max characters per line | 16–80 (default 42) | |
| Max lines | 1–3 (default 2) | |
| Burn-in style | white with black outline (default), text on a dark box, bold yellow | Used only by Render |
| Burn-in font size | small, medium (default), large | Used only by Render |

The burn-in style and font size are stored with the subtitle output as metadata. The SRT/VTT file itself is plain.

## Output

- **Asset:** `subtitle-<id>.srt` or `.vtt`, with provider `local`, model `subtitle`, and its project, run and step lineage. It downloads through `GET /api/assets/{id}` as an attachment.
- **Port `subtitle_asset`:** carries the whole description Render needs:

```json
{"id": "…", "asset_id": "…", "filename": "subtitle-1a2b3c4d.srt", "content_type": "application/x-subrip",
 "format": "srt", "cue_count": 12, "duration": 24.0, "timing": "audio",
 "style": {"preset": "classic", "font_size": "medium"},
 "cues": [{"start": 0.0, "end": 2.1, "text": "…", "scene_index": 1}],
 "segments": [{"scene_index": 1, "start": 0.0, "end": 2.1, "source": "audio"}]}
```

- **Storage:** the file is written while the run advances. If that transaction rolls back, the file is deleted. When the workspace's storage is full, the step is blocked instead.
- **Retry:** a subtitle file is not paid media, so it does not stop a retry after a refund.

## Interface

- **Node:** the first cues, the format and the cue count.
- **Inspector:** format, cue count, duration, timing source, a download link and a preview of every cue with its time and scene.

There is no subtitle editor in this phase.

## Limitations

- Timing follows narration, clip or scene lengths, not the actual words spoken.
- One language per file: subtitles show the script's language; there is no translation step for subtitles.
- The file has no styling; styling is applied only when Render burns it in.
