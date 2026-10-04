# Repurposing Existing Content

The **Repurpose Existing Content** template (`repurpose`) turns an article or a web page into a short vertical video. It uses the same pipeline as the YouTube Short template, but the script is written from a source instead of an idea:

```text
URL Source → AI Writer (source) → Scene Splitter ─┬→ Video ──────────────────┐
                                                   ├→ Voice ─┬────────────────┤
                                                   └→ Subtitle ←┘ (audio)      ├→ Render → Review → Publish
                  AI Writer → Metadata ────────────────────────────────────────────────────────────┘
```

| Step | Default |
| --- | --- |
| URL Source | empty: set the page address before running |
| AI Writer | YouTube Shorts, about 60 s of speech, written from the page text; the project topic still guides it |
| Scene Splitter | 5 s scenes, at most 12 |
| Video | 9:16, 6 s clips |
| Subtitle | 32 characters per line |
| Publish | private by default; the owner publishes from the Publishing page |

## Other sources

Replace the URL Source with any other source step, and connect its **Text** output to the AI Writer's **Source** input:

- **Text Source**: paste notes or an existing script.
- **Uploaded Media Source**: use a TXT, MD, SRT or VTT file.
- **Uploaded Media Source → Transcript**: use a podcast or a talk you recorded.

To shorten or translate first, put Summarize, Rewrite or Translate between the source and the AI Writer.

## Setup

- Workers:
  - the text, video, voice and render workers, as in `docs/SOCIAL_VIDEO_WORKFLOW.md`;
  - `python -m app.source_worker`, for URL Source and Transcript.
- AI models: one Text, one Video and one Voice model. Add a Transcription model only if you transcribe media.
- Credits: the same as the YouTube Short (1 per text step, 10 per clip, 1 per narration). Fetching a page is free. A transcription costs `TRANSCRIPTION_CREDITS_PER_JOB`.

## Rights and safety

Repurpose only content you own or are authorized to use. URL Source reads public pages as described in `docs/CONTENT_SOURCES.md`:

- no JavaScript, no login, no paywall or bot-check bypass;
- no private or local addresses.

It does not download video from streaming sites. To use a video, upload a file you are allowed to use.
