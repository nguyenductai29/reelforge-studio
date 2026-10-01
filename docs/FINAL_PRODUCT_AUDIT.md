# Final product audit (Phase 16)

> Snapshot: branch `feat/studio-foundation`, after Phases 14–16, 2026-10-01. Database head: `0015_admin_payments_profiles`.

**Rule applied:** every control in the production interface works. An unfinished feature was either built now, when it reuses existing capabilities cheaply, or removed from the interface. No "coming soon" badge, banner, disabled placeholder switch or preview-only template remains. `tests/test_product_audit.py` enforces this. It fails on:

- a `SoonBadge` or `ComingSoonBanner`;
- a "coming soon" string in any language;
- a hard-coded disabled switch;
- a step-library entry or preset the backend cannot run;
- a template without a backend graph;
- Instagram in the channel lists.

## 1. Fully implemented product features

**Content creation**

- **Templates** (`/create`); each creates a workflow the backend builds and runs:
  - Social video;
  - YouTube Short and YouTube (16:9);
  - TikTok video;
  - Facebook Reel;
  - Repurpose existing content;
  - Movie Recap;
  - **Movie Review**: Movie Recap with the review style and light spoilers;
  - **Article → Video**: URL source → AI Writer → scenes → AI image per scene → voice → subtitles → render;
  - **Product Video**: idea → 30-second promo script → scenes → images → voice → subtitles → render;
  - Blank.
- **Step library**: 47 entries, all executable. Several are presets of an existing step rather than new node types:

  | Entry | Step it adds |
  | --- | --- |
  | Short script | AI Writer, 60 s, YouTube Shorts |
  | Long script | AI Writer, 600 s, YouTube |
  | Movie review | Recap Script, review style, light spoilers |
  | Ending explained | Recap Script, explainer style, full spoilers |
  | Thumbnail | Image, 16:9, high quality |
  | Key moments | Story Analysis |
  | Text to video | Video |
  | Merge clips | Render |
  | Preview | Review |
  | Schedule post | Publish (scheduling happens in the publish dialog) |
  | Upload image | Media source (images are now accepted) |

- **Background Music** (`music` step): an uploaded MP3, WAV or OGG file the user owns. It loops for the length of the video and is mixed under the narration at 1–100 % (default 15 %) by Render. Without music, the render command is unchanged.
- **Image slideshows:** Render accepts still images as scenes. Each image is shown for its scene's narration length, or a share of one narration, or `RENDER_STILL_SECONDS` (default 5).
- **Workspace content defaults** (Settings → Content defaults): platform, tone and target length. Writing steps that leave these settings empty use them.

**Library and media**

- The Library **Scripts** tab lists the AI-written scripts of every run, server-paginated (`GET /api/scripts`).
- A project shows its latest script.
- **Add to project** for uploaded files (`PATCH /api/assets/{id}`). Generated media stays with its run.

**Publishing**

- YouTube, TikTok (inbox drafts) and Facebook Page Reels connect through their official OAuth and APIs, and publish one approved video to several channels.
- Scheduling, rescheduling and cancelling work before the upload starts.
- The publishing queue is paginated.
- The Calendar loads only the visible date range and shows scheduled, queued, uploading, succeeded, failed and cancelled publications (cancelled ones are listed, not placed on the grid).
- **Default publishing time** (Settings → Publishing) prefills the scheduler.

**Account and billing**

- **Display name** (Settings → General), shown in the admin console.
- Billing offers **VietQR / Bank transfer** (payOS) and **Bank card** (OnePAY). Only configured methods are offered.
- The order history is paginated: plan, method, amount, status, created, paid, order code.
- See [PAYMENTS.md](PAYMENTS.md).

**Administration**

- The console fits the window. It has six tabs: Users, Studios & credits, Plans, Payments, Credit reconciliation and Operations.
- Tables are server-paginated, with server-side search and filters and dialogs for each action.
- Counts come from `COUNT` queries.
- See [OPERATIONS.md](OPERATIONS.md#admin-console-phase-15).

**Still available from earlier phases**

- Default models per task.
- Storage usage per workspace and quota.
- Worker health and job views.
- Dry-run media maintenance.
- Credit reconciliation.

## 2. Features intentionally hidden/deferred

They are not shown anywhere in the production interface. Their dictionary labels remain only where they cost nothing.

| Feature | Decision | Why |
| --- | --- | --- |
| **Instagram** publishing | Hidden (decision B) | Instagram Reels needs an Instagram professional account linked to a Page, `instagram_content_publish`, a separate container/publish flow and its own Meta app review. That is new scope beyond the Facebook Page integration. YouTube, TikTok and Facebook are unchanged |
| YouTube URL input | Hidden | Downloading YouTube media conflicts with YouTube's terms. Users upload media they own |
| Research, Scene planner, Storyboard | Hidden | No backend. AI Writer, Story Analysis and Scene Splitter cover the released flows |
| Stock media | Hidden | Needs a licensed stock provider |
| Image → video | Hidden | No image-conditioned video provider is integrated |
| Voice clone | Hidden | Consent, abuse and provider work out of scope |
| Sound effects, Audio mixer | Hidden | Background Music covers the released need. Generative effects and multi-track mixing are not built |
| Crop/resize, Aspect ratio, Overlay text, Transition, Timeline | Hidden | They need an editing timeline. Render already sets the aspect ratio from the workspace or the step |
| Download step | Hidden | Not a pipeline step. Final videos download from Library, Media and the project page |
| Single-tool pages `/ai/*` | Redirect to `/create` | The same features are templates. The old pages had disabled forms |
| Workspace brief chips, AI rewrite actions, storyboard tab, AI assistant panel | Removed | No backend. The project's title and topic remain editable |
| Settings: teammates, 2FA, storage retention, "auto-schedule at best time", recap/thumbnail/provider-ID toggles | Removed | Team collaboration and 2FA are larger features. Automatic deletion is not allowed without an explicit policy; dry-run maintenance remains. The others had no behaviour |
| Legacy `script` node | Kept only so old workflows open | The editor no longer offers it. A run blocks it with a reason, and the inspector suggests replacing it |
| Crypto payments | Not offered | Out of scope |

## 3. Remaining live/manual verification

All Phase 14–16 tests are offline. No paid or live API was called. An operator must verify:

**Payments**

- OnePAY sandbox, then one real payment:
  - checkout;
  - return;
  - IPN through the proxy (`responsecode=1`);
  - QueryDR;
  - a cancelled payment;
  - credits posted once.
- payOS: one payment after deployment.

**Rendering**

- A real FFmpeg render with an image slideshow plus Background Music: the music volume under the narration, the loop, and the still timing.
- Movie Recap and Movie Review on a real source video.

**Templates**

- One live run each of Article → Video and Product Video. They use paid text, image and voice providers.

**Publishing and admin**

- TikTok inbox and Facebook Reel uploads, and scheduled publishing on time. Still open from Phases 12–13.
- The Admin layout on real devices. It was checked in headless Chromium on an isolated stack with 62 accounts and 46 orders,
  at 1366×768, 1680×1050 and 390×844, on every tab: the page never scrolls, the table body scrolls inside it,
  the headers stay sticky and the pagination stays visible. The card checkout was checked through the UI against a
  fake gateway that the browser never reached: a forged return, a signed paid return without QueryDR, and a signed
  cancellation.

**Providers** (see `IMPLEMENTATION_STATUS.md` → Live Provider Verification)

- Already operator-verified: Gemini text, Runway `gen4.5` video and `gen4_image`.
- Still mocked only: OpenAI and Anthropic text, Gemini TTS, Whisper transcription, fal, Runware and Replicate video, and the YouTube upload.

## 4. Payment providers and required credentials

| Provider | Method | Credentials | Where | Callback URLs |
| --- | --- | --- | --- | --- |
| payOS | VietQR / Bank transfer | `client_id`, `api_key`, `checksum_key` | `payos` object in `instance/bootstrap.json` | Webhook `https://<frontend>/api/webhooks/payos` |
| OnePAY | Bank card | `ONEPAY_MERCHANT_ID`, `ONEPAY_ACCESS_CODE`, `ONEPAY_HASH_KEY` (hex). Recommended: `ONEPAY_QUERY_USER`, `ONEPAY_QUERY_PASSWORD`. Optional: `ONEPAY_PAYMENT_URL`, `ONEPAY_QUERY_URL` | `.env.runtime` of the API | IPN `https://<frontend>/api/webhooks/onepay`; return `https://<frontend>/api/billing/onepay/return` |

Plan prices (VND per 30 days) are set in Admin → Plans. A plan without a price cannot be bought. Credentials are never returned to the browser; Admin → Payments shows only Configured or Missing.

## 5. AI/provider credentials

All keys go in the shared `.env.runtime` (`EnvironmentFile=` for every unit) and are never stored in the database.

| Task | Provider | Variable |
| --- | --- | --- |
| Text (AI Writer, Summarize, Rewrite, Translate, Hook, Title, CTA, Metadata, Story Analysis, Recap Script) | OpenAI, Anthropic, Gemini | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` |
| Transcription | OpenAI Whisper | `OPENAI_API_KEY` |
| Voice | Gemini TTS | `GEMINI_API_KEY` |
| Image | Runway `gen4_image` | `RUNWAYML_API_SECRET`, `RUNWAY_OUTPUT_HOSTS` |
| Video | Runway, fal, Runware, Replicate (Dola is experimental) | `RUNWAYML_API_SECRET`, `FAL_KEY`, `RUNWARE_API_KEY`, `REPLICATE_API_TOKEN` (`DOLA_*`) |

The **Model AI** page enables models per workspace. Settings → AI sets the default model per task.

## 6. Social OAuth approvals/configuration

`REELFORGE_TOKEN_ENCRYPTION_KEY` (Fernet) encrypts every token and upload session. Back it up; it must never change.

| Channel | Variables | Approval |
| --- | --- | --- |
| YouTube | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` | `youtube.upload` scope. Unverified Google API projects can upload only private videos; the Publishing page shows the visibility YouTube applied |
| TikTok | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` (`https://<frontend>/channels/callback/tiktok`), `TIKTOK_APPROVED_SCOPES` | Login Kit and the Content Posting API approved for `video.upload`. Videos go to the creator's inbox as drafts |
| Facebook | `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`, `FACEBOOK_REDIRECT_URI` (`https://<frontend>/channels/callback/facebook`) | `pages_show_list`, `pages_read_engagement` and `pages_manage_posts`. These need Meta app review for accounts outside the app's roles |
| Instagram | — | Hidden; see § 2 |

## 7. Workers/services required

Run each as its own systemd unit. Start only the ones you use; Admin → Operations shows each worker's heartbeat.

| Process | Needed for |
| --- | --- |
| API (`uvicorn app.main:app`) and Next.js frontend | Always |
| `python -m app.text_worker` | Every writing step |
| `python -m app.image_worker` | Image steps, including slideshow templates |
| `python -m app.video_worker` | AI video clips |
| `python -m app.voice_worker` | Narration |
| `python -m app.render_worker` | Render (including Background Music and slideshows) and source-clip extraction (Movie Recap/Review) |
| `python -m app.source_worker` | URL sources and transcription |
| `python -m app.youtube_worker` | YouTube uploads |
| `python -m app.social_worker` | TikTok and Facebook uploads |
| `python -m app.scheduler_worker` | Scheduled publications |

Payments need no worker: webhooks, the IPN and status checks are handled by the API.

## 8. Current DB migration head

`0015_admin_payments_profiles` adds:

- `user_profiles`, holding an optional display name;
- indexes `ix_workspaces_owner_id`, `ix_payment_orders_created_at` and `ix_payment_orders_provider_status`.

Card orders reuse `payment_orders`, so there is no other schema change. Apply it with `python -m alembic upgrade head` before restarting the services. `tests/test_admin_payments_migration.py` checks the upgrade from `0014_channels_scheduling_ops` and the downgrade on SQLite; set `REELFORGE_TEST_DATABASE_URL` to run it on PostgreSQL.

## 9. Storage/FFmpeg requirements

- **FFmpeg:** system `ffmpeg` and `ffprobe` (`RENDER_FFMPEG_PATH` / `RENDER_FFPROBE_PATH`), plus Noto fonts for burned-in subtitles. On Ubuntu: `sudo apt install -y ffmpeg fonts-noto-core fonts-noto-cjk`. Check with `python -m app.render_worker --check`. They are needed where runs advance and media is cut: the API, the render worker (renders and clip extraction) and the source worker (transcription).
- **Media storage:** shared by the API and all workers (`/srv/data/reelforge/media` in production). `WORKSPACE_MEDIA_QUOTA_BYTES` defaults to 1 GiB per workspace, and uploads are capped at 100 MiB each. Music uploads count toward the quota.
- **Backups:** back up PostgreSQL and the media directory together, and the Fernet key separately.
- **Cleanup:** `python -m app.media_maintenance` is a dry run unless `--apply` is given. Nothing deletes valid assets automatically.

## 10. Known limitations

**Payments**

- Card payment has not been live-tested.
- Without QueryDR credentials, a card order waits for the IPN.
- Refunds and chargebacks are handled outside the app.
- Renewal is manual, not recurring.

**Background Music**

- One track per render, looped.
- No ducking curve, fade or effect library.

**Slideshow templates**

- Images are still frames; there is no Ken Burns motion or transitions.
- Product Video starts from an idea; uploaded product photos must be connected manually through a media source.

**Admin console**

- User search matches email only. Display names are shown but not searched.
- `LIKE '%q%'` search scans the table. This is fine for thousands of accounts; it would need a trigram index for much more.

**Product scope**

- No team collaboration, 2FA, password recovery or email delivery.
- TikTok uploads land as inbox drafts, not direct posts.
- YouTube uploads from an unverified Google project stay private.

## 11. Production deployment checklist

1. **Before the update.** Back up PostgreSQL, the media directory and `REELFORGE_TOKEN_ENCRYPTION_KEY`.
2. **Update.** Pull the branch, then run `pip install -r requirements.txt` and `python -m alembic upgrade head`. Check that `python -m alembic current` prints `0015_admin_payments_profiles`.
3. **Frontend.** In `frontend/`, run `npm ci` and `npm run build`.
4. **Runtime file.** Fill in `.env.runtime` from `.env.runtime.example`: provider keys, OAuth apps, and the OnePAY variables if card payment is offered.
5. **OnePAY URLs.** Register the IPN and return URLs with OnePAY; keep the payOS webhook URL.
6. **System Settings.** Set `frontend_origin` to the public HTTPS URL and turn secure cookies on.
7. **Restart.** Restart the API, the frontend and every worker you use. `python -m app.render_worker --check` must pass.
8. **Admin checks.**
   - Admin → Operations: every enabled worker shows as running.
   - Admin → Payments: the intended providers show as **Configured**.
   - Admin → Plans: Standard and Pro have prices.
9. **Live payments.** Run the checks in § 3, starting with the OnePAY sandbox, before accepting customers.
10. **Smoke workflow.** Upload a short music file and an image, run Product Video or Article → Video with Background Music connected, review it, and publish privately.

## Remaining SoonBadge / ComingSoonBanner

**Count: 0.** Both components were deleted from `frontend/src/components/reelforge/primitives.tsx`. No source file, dictionary or test fixture refers to them, and `tests/test_product_audit.py` fails if one is reintroduced.

The only other occurrences are in `frontend/.next-dev/`, the git-ignored build cache of a running `next dev` server. They disappear on its next rebuild; they are not shipped.
