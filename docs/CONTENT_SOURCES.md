# Content Sources

Phase 10 lets a workflow start from content you already have, not only from a project topic. Four source steps produce a **source** value (port type `source`) and its plain **text** (port type `text`). Any text step can read that text, for example AI Writer, Summarize, Rewrite or Translate.

```text
{"source_type": "text|url|document|video|audio|transcript", "title": str, "text": str,
 "language": str | null, "asset_id": str | null, "source_url": str | null,
 "segments": [{"start": s, "end": s, "text": str}] | null, "metadata": {...}}
```

Text is capped at 60,000 characters (`truncated` is set when it was cut). A source never contains a local file path.

## The steps

| Step (type) | Input | What it does | Cost |
| --- | --- | --- | --- |
| Text Source (`source_text`) | title, pasted text (up to 7,000 characters), language | completes at once | free |
| URL Source (`source_url`) | one `https://` address | the source worker fetches the page and extracts its readable text | free |
| Uploaded Media Source (`source_media`) | one uploaded file | TXT/MD/SRT/VTT are read at once; audio and video are passed on (video also as `video_assets`) | free |
| Transcript (`transcribe`) | a source or an audio/video asset | speech to text with timestamped segments, through the workspace's transcription model | `TRANSCRIPTION_CREDITS_PER_JOB` (default 2) |

For longer pasted text, upload a `.txt` or `.md` file on the **Media** page and use Uploaded Media Source.

A Transcript step whose source already has text (a document, a page or subtitles) passes it through for free, and no job is created. An SRT or VTT file keeps its cue times as segments, so a Movie Recap can work from subtitles without paid transcription.

## Uploads

Besides images, MP4/WebM video and MP3/WAV/OGG audio, the upload endpoint (`POST /api/assets`) accepts text documents:

| Extension | Stored type | Checked |
| --- | --- | --- |
| `.txt` | `text/plain` | UTF-8, no NUL bytes, at most 2 MB |
| `.md`, `.markdown` | `text/markdown` | same |
| `.srt` | `application/x-subrip` | same, and at least one `-->` cue timing |
| `.vtt` | `text/vtt` | same, and a `WEBVTT` header |

Browsers often send these files with no type or `application/octet-stream`. For these extensions, the extension decides the stored type.

A source step's file setting (`asset_id`, a new `asset` field type) must name an upload of the same workspace with a type the step reads. Saving a workflow rejects any other file with `422 invalid_asset`, including a file of another workspace.

## Fetching web pages safely

`app/sources.py` and the source worker apply these rules to every URL, and again after every redirect:

- Only `https://` on port 443, without `user:password@`.
- Names such as `localhost`, `*.local`, `*.internal` and `metadata.google.internal` are refused.
- Every address the host resolves to must be public. Loopback (`127.0.0.0/8`, `::1`), private IPv4 and IPv6 (including `fc00::/7`), link-local (`169.254.0.0/16`, `fe80::/10`), cloud metadata addresses, multicast, reserved and unspecified addresses are refused. This also covers IPv4-mapped IPv6 addresses.
- The worker connects to the address it checked and sends the host name in `Host` and TLS SNI. The certificate is still verified for that name. A DNS answer that changes between the check and the connection (DNS rebinding) therefore cannot redirect the request.
- Redirects are followed by hand, at most 3, and each one is checked again.
- Only `text/html`, `application/xhtml+xml` and `text/plain` responses are read. They are read under a 15-second timeout (5 seconds to connect) and are cut off above 2 MB.
- No JavaScript runs, no browser is used, and no paywall, login or bot check is bypassed. A page that needs any of these has no readable text and fails with `no_text`.

Text extraction skips `script`, `style`, `nav`, `header`, `footer`, `aside` and form elements, and prefers the `article` or `main` element. The page title comes from `og:title` or `<title>`.

The URL is checked when the workflow is saved (readiness shows `blocked_url`) and again when the step starts. A blocked URL stops the step before any job is queued.

## Transcription

Transcription is a provider-neutral task (`app/providers/transcription/`). Add a model on the **AI Models** page under **Transcription**: OpenAI · Whisper (`openai`, `whisper-1`) needs `OPENAI_API_KEY`.

When a Transcript step runs:

1. It holds `TRANSCRIPTION_CREDITS_PER_JOB` credits (`transcription-reserve:<step>:single`) and queues one durable job (`source:<step>:transcript`).
2. The source worker probes the file with ffprobe and refuses media without an audio track (`no_audio`) or longer than `TRANSCRIPTION_MAX_SECONDS` (default 3 hours, `too_long`).
3. FFmpeg extracts mono 16 kHz MP3 audio in pieces of at most 20 minutes, in `<media>/.source-tmp/<job>`. Video works the same way as audio.
4. Each piece is sent to the provider (`response_format=verbose_json`). Its segment times are shifted by the piece's offset, and the results are joined.
5. The step outputs `transcript`: `{text, language, segments[{start, end, text}], asset_id, metadata.duration}`. It also outputs its `text`.

The temporary folder is always removed.

### Credits and failures

Credits follow the rules of every other paid step:

| Outcome | Credits | Step result |
| --- | --- | --- |
| Success | charged as usage `transcription:<step>:single` | completed |
| Definite rejection (bad key, invalid request, too many attempts) or a local problem (FFmpeg missing, no audio, too long) | refunded (`transcription-refund:…`) | failed |
| Lost answer, or the worker crashed after the provider call started | held | needs attention; appears in **Admin → Credit reconciliation** and is never sent again automatically |
| Rate limiting | held | tried again with a delay |

## Running it

```bash
python -m app.source_worker          # URL Source and Transcript jobs (needs FFmpeg for transcription)
python -m app.source_worker --once   # one due job
```

See `docs/home-server-deployment.md` for the systemd unit, and `docs/REPURPOSING.md` and `docs/MOVIE_RECAP.md` for the templates built on these steps.
