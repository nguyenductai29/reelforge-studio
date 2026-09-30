# Social Video Workflow

Phase 9 joins the existing steps into one creator workflow, from a topic to a YouTube video:

```text
Project topic → Idea → AI Writer → Scene Splitter ─┬→ Video ──────────────────┐
                                                   ├→ Voice ─┬────────────────┤
                                                   └→ Subtitle ←┘ (audio)      ├→ Render → Review → Publish
                  AI Writer → Metadata ────────────────────────────────────────────────────────────┘
```

Publishing stays a human decision. The run stops at Review until a person approves the final video. Publish then hands the video and its prepared metadata to the Publishing page, and nothing is uploaded until the owner presses **Publish to YouTube**.

## Starter templates

**Workflows → From a template** offers two starter workflows, built by the backend (`app/workflow/templates.py`, `POST /api/workflows` with `template`):

| | YouTube Short (`youtube_short`) | YouTube landscape video (`youtube_landscape`) |
| --- | --- | --- |
| AI Writer | YouTube Shorts, about 50 s of speech | YouTube, about 2 min of speech |
| Scene Splitter | 5 s scenes, at most 12 | 7 s scenes, at most 20 |
| Video | 9:16, 6 s clips | 16:9, 8 s clips |
| Subtitle | 32 characters per line | 42 characters per line |
| Render | vertical (the clips' frame) | horizontal |
| Metadata | YouTube Shorts | YouTube |
| Publish | private by default | private by default |

- Every setting uses the node's normal schema, and all of them can be changed in the inspector.
- `GET /api/workflow-templates` lists the templates.
- The older templates (idea → video → review) and existing workflows are unchanged.

**Models are never baked in.** Templates leave every step's model empty. At run time each step uses the first enabled model for its task (text, video, voice), in the order the workspace added them. A model chosen in a step's settings is always kept. The Run dialog lists the model each step will use and marks the ones picked automatically.

## Setup

1. **Workers**:
   - API and frontend;
   - `app.text_worker` (AI Writer and Metadata);
   - `app.video_worker`;
   - `app.voice_worker`;
   - `app.render_worker` (FFmpeg and Noto fonts installed);
   - `app.youtube_worker` for publishing.

   Subtitles need no worker. See `docs/home-server-deployment.md`.
2. **AI models** (AI Models page): enable one Text model, one Video model and one Voice model (Google · Gemini 2.5 Flash TTS). The runtime file needs their keys, for example `GEMINI_API_KEY`, `RUNWAYML_API_SECRET` and `RUNWAY_OUTPUT_HOSTS`.
3. **YouTube** (Channels page): connect a channel. This needs `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` and `REELFORGE_TOKEN_ENCRYPTION_KEY`, and the `youtube.upload` scope.
4. **Project:** create one with a title and a topic. The Idea step reads them at run time; they are not copied into the workflow.
5. **Credits:**
   - 1 per text step (AI Writer and Metadata);
   - 10 per clip and 1 per narration, per scene;
   - render and subtitles are free.

   A 3-scene Short costs 1 + 1 + 3 × 10 + 3 × 1 = 35 credits with the defaults.

## Running

1. Open the workflow and press **Run**. The dialog shows:
   - the project to use and its topic, with a link to edit it;
   - the model each step will use;
   - any readiness problems;
   - the credit estimate.
2. The canvas shows each step's state. The run bar has a **Summary** panel:
   - steps done and the steps running now;
   - active jobs and elapsed time;
   - what was made (script words, scenes, images, clips, narrations, subtitle cues);
   - the final video;
   - the run's credits;
   - failed, blocked and needs-reconciliation steps, with their reasons (a Render failure shows FFmpeg's message).
3. When Render finishes, **Download final MP4** appears; it uses the existing asset endpoint. The Review step previews the final video only.
4. Press **Approve video**. The Publish step completes as a hand-off, and the run bar offers **Prepare publishing**.

The summary comes from `GET /api/workflow-runs/{id}/summary` and is derived from stored steps, jobs and ledger entries; there is no new table.

## Metadata

The **Publishing metadata** step (`metadata`) is a text step: same text worker, same credits (`text-reserve:<step>`), same reconciliation. It asks the text model for one JSON object with `title`, `description` and `tags`, using the project topic and the script.

Its settings are language, platform, number of tags, whether the description ends with a call to action, and the usual model, tone and instructions. The reply is fitted to YouTube's limits and never fails the step: a reply that is not JSON gives its first line as the title and the rest as the description.

The **Publish** step prepares the metadata the Publishing form starts with, in this order:

1. the step's own settings (Title, Description, Tags, Visibility): values the user typed are never replaced;
2. the connected Metadata step;
3. connected title and description text;
4. the project title.

The form can still be edited before publishing.

## Publishing to YouTube

**Prepare publishing** (run bar, Publish step inspector or the Publishing page) opens the form with:

- **Video:** the final render when the run has a completed Render step. Otherwise (older workflows) the approved clip. When a render exists, the API refuses to publish a scene clip.
- **Title:** 1–100 characters, one line, no `<` or `>`.
- **Description:** at most 5,000 bytes, no `<` or `>`.
- **Tags:** comma-separated, 500 characters in total counted the way YouTube counts them (quotes around tags with spaces, commas between tags), no commas, `<` or `>` inside a tag.
- **Visibility:** private (default), unlisted or public. These are sent as `status.privacyStatus` with the existing `youtube.upload` scope.

The form checks these limits as you type, and the API checks them again before anything is queued (422 with `invalid_title`, `invalid_description`, `invalid_tags` or `invalid_privacy`).

Unchanged from before:

- the upload itself: the durable `publish:youtube:<run>` job, the YouTube worker, resumable sessions encrypted at rest, and the OAuth connection generation check;
- one YouTube publication per run: repeating the same request returns it, and different metadata is refused (409).

After the upload, the Publishing page shows:

- the state: queued, uploading, succeeded, failed or needs attention;
- the YouTube video ID, a **Watch on YouTube** link and a YouTube Studio link;
- YouTube's upload status (`uploaded` means YouTube is still processing);
- the visibility YouTube actually applied.

Google keeps videos uploaded through **unverified** API projects private. ReelForge accepts a visibility that is more private than requested and says so; a more public one is treated as unexpected and needs attention. No token, session URL or job payload is ever returned.

## Failure recovery

- **A step fails, is blocked or needs reconciliation:** the Summary panel names the step and its reason. Paid steps follow `docs/CREDIT_RECONCILIATION.md`.
- **A failed render:** its step shows the FFmpeg message (without paths). Render prices, if any, are refunded.
- **A failed upload that never sent media** (for example YouTube rejected the metadata, or the account had no permission) can be retried from the Publishing page. Title, description, tags and visibility can be corrected first.

  A retry queues only a new upload job (`publish:youtube:<run>:retry:<job>`) from the same final MP4. The script, images, clips, narration, subtitles and render are not generated again, and no credit is reserved or charged. The test suite checks that jobs, ledger, usage, assets and step outputs are unchanged.
- **An upload whose outcome is uncertain** (a session was saved, or bytes were sent) is marked needs attention and is never retried automatically. Check YouTube Studio first.
- **Retrying the run itself** from the run bar starts a new run and generates everything again. Use it only when the content itself must change.

## Credits

The Summary panel shows the run's ReelForge credits, never provider money:

- **Reserved:** held when paid steps were queued.
- **Used:** usage events for finished work, including charges an admin confirmed.
- **Refunded:** automatic refunds and reconciliation refunds.
- **Held:** reserved but neither used nor refunded yet (running, or awaiting an admin decision).

A ledger or usage entry belongs to the run when its reference names the run or one of its steps. Publishing to YouTube costs no credits.

## Compatibility

- Existing workflows and runs keep working. A workflow does not need Voice, Subtitle, Render, Metadata or Publish; `Idea → AI Writer → Scene Splitter → Video → Review` still runs, and its approved clip can be published.
- The Publish node keeps its earlier port names (`video`, `title`, `description`) and adds `metadata`.
- Before Phase 9, the Publish node always blocked after approval, leaving the run *blocked*. It now completes as a hand-off, so the run is *completed*. Publishing already accepted both run states.
- Migration `0013_publication_metadata` adds `privacy_status` (default `private`), `tags` (default `[]`), `remote_status` and `remote_privacy` to `publications`. Existing publications keep their meaning.

## Known limitations

- **YouTube only:** TikTok and Facebook publishing, scheduling, thumbnails, playlists and categories are not implemented.
- **Processing status:** `uploaded` is YouTube's status right after the upload. ReelForge does not poll processing afterwards: that would need a read scope (`youtube.readonly`) beyond the `youtube.upload` scope it asks for.
- **One publication per run and channel.** Publishing the same render again needs a new run.
- **Unverified Google projects** publish privately whatever visibility is chosen; the Publishing page shows the actual visibility.
- **Model choice:** automatic resolution is the first enabled model per task; there is no workspace-wide preferred model yet.
- **Credit estimates:** the Run dialog shows per-scene credits; the scene count is known only after the script is split.
