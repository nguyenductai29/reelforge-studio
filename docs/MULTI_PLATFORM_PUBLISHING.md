# Multi-platform Publishing

Phase 12 adds TikTok and Facebook Page Reels next to YouTube. Every upload uses the platform's official API. Nothing automates a website, and nothing is posted without the owner pressing **Publish** on an approved video.

## Channels

The **Channels** page (`GET /api/channels`) shows each channel with one of four statuses:

| Status | Meaning |
| --- | --- |
| `connected` | ready to publish |
| `not_connected` | nobody authorized yet |
| `configuration_required` | the server lacks the app credentials or `REELFORGE_TOKEN_ENCRYPTION_KEY` |
| `authorization_required` | the grant expired (`expired`), lacks the upload scope (`missing_scope`), or, for Facebook, no Page is chosen (`page_required`) |

Only the workspace owner connects or disconnects a channel:

- `GET /api/channels/{channel}/authorization` returns the consent URL;
- `POST /api/channels/{channel}/callback` completes it, called by the frontend page `/channels/callback/{channel}`;
- `PUT /api/channels/facebook/page` chooses the Page;
- `DELETE /api/channels/{channel}` disconnects.

The OAuth state is single-use, lasts ten minutes, is stored hashed, and is bound to the owner and the channel. The state is consumed before the token endpoint is called.

Tokens are encrypted at rest with `REELFORGE_TOKEN_ENCRYPTION_KEY` (Fernet), in `channel_connections` (migration 0014). No token, refresh token or upload URL is ever returned by the API or written to a log.

### TikTok

- **App:** a TikTok developer app with Login Kit and the Content Posting API, approved for `video.upload`. Set:
  - `TIKTOK_CLIENT_KEY`;
  - `TIKTOK_CLIENT_SECRET`;
  - `TIKTOK_REDIRECT_URI`: `https://<frontend>/channels/callback/tiktok`, registered in the portal;
  - `TIKTOK_APPROVED_SCOPES` (default `user.info.basic,video.upload`).
- **Scopes requested:** `user.info.basic,video.upload`.
- **Tokens:** access tokens last about a day and are refreshed automatically with the refresh token, which is rotated and stored again. When the refresh token expires (about a year), the channel shows `authorization_required`.
- **Upload:** an **inbox draft**. The worker initializes the upload, sends the file in chunks (5–64 MB, one chunk below 64 MB), then polls the status:
  - `SEND_TO_USER_INBOX` is recorded as `succeeded` with `remote_status = sent_to_inbox` and no `published_at`. The creator finishes the post (caption, visibility) in the TikTok app.
  - `PUBLISH_COMPLETE` is recorded as published.
  - ReelForge never posts directly to a TikTok profile.
- **Caption:** the inbox API takes no caption. The caption you prepare (at most 2,200 characters with hashtags) is kept on the publication for the creator to copy. Visibility is always `private` (a draft).

### Facebook

- **App:** a Meta app with Facebook Login. Set:
  - `FACEBOOK_APP_ID`;
  - `FACEBOOK_APP_SECRET`;
  - `FACEBOOK_REDIRECT_URI`: `https://<frontend>/channels/callback/facebook`.
- **Scopes requested:** `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`.
- **Pages:** the code is exchanged for a user token, then a long-lived one. The Pages the user can create content on are listed, and their Page tokens are stored encrypted. With one Page it is chosen automatically; with several, the owner picks one on the Channels page. Choosing another Page counts as a new connection: uploads already queued for the old Page stop with `connection_changed`.
- **Upload:** a Page Reel in four steps, each saved before the next (Graph API `v26.0`):
  1. start;
  2. transfer the file;
  3. finish with `video_state`: `PUBLISHED` for visibility `public`, or `DRAFT` for `private`;
  4. poll the status until Meta reports it published, or processed for a draft.
- **Description:** at most 5,000 characters with hashtags.

## Metadata per channel

The Metadata step generates YouTube's title, description and tags. In the same JSON reply it also generates a short TikTok caption and a Facebook description.

The Publish step hands over `metadata` (YouTube, unchanged since Phase 9) and `platforms` (`{youtube, tiktok, facebook}`). Each channel's values come from, in order:

1. values typed in the Publish step's settings (`title`, `description`, `tags`, `tiktok_caption`, `facebook_description`). These are never replaced;
2. the connected Metadata step;
3. YouTube's values.

Everything is fitted to the channel's limits: TikTok and Facebook tags become one-word hashtags, at most 30.

## Publishing

The publish dialog:

- chooses channels (connected ones only, and only those without a publication for this run);
- shows each channel's metadata on its own tab;
- posts **now** or at a scheduled time (see `docs/SCHEDULING.md`).

It calls:

```http
POST /api/publications
{"run_id": "...", "asset_id": null, "scheduled_for": null,
 "targets": [{"channel": "youtube", "title": "...", "description": "...", "tags": [], "privacy_status": "private"},
             {"channel": "tiktok", ...}, {"channel": "facebook", ...}]}
```

- Every target is validated against its channel first. A problem returns `422` naming the channel and field, for example a TikTok visibility other than `private` or a caption that is too long.
- Every channel must be connected, else `409 channel_not_connected`.
- Each channel then gets one publication (unique per run and channel) and its own job (`publish:<channel>:<run>`).
- Jobs are independent: the YouTube worker handles `publish:youtube:` and the social worker handles `publish:tiktok:` and `publish:facebook:`. A failure on one channel never affects another.
- Repeating the same request returns the existing publications.

### Failures and retries

| What happened | State | Retry |
| --- | --- | --- |
| Rejected before any media was sent (bad request, configuration, rate limit after retries) | `failed` | allowed (`POST /api/publications/{id}/retry`, metadata can be corrected) |
| TikTok's answer to the upload request was lost (no video bytes sent yet) | `needs_attention` | allowed |
| TikTok reported the upload failed | `failed` (`remote_failed:<reason>`) | allowed |
| Media sent and the outcome is unclear, or Facebook's answer to "finish" was lost (it may be live) | `needs_attention` | not allowed: check the platform first |
| The connection changed after queueing | `needs_attention` (`connection_changed`) | allowed when nothing was sent |

Transient errors are retried with backoff, at most 6 times per step. Progress is saved encrypted on the publication after every step, so a restarted worker resumes at the next chunk or poll.

## Running it

```bash
python -m app.youtube_worker   # YouTube
python -m app.social_worker    # TikTok and Facebook
```

The API and the workers need the same `REELFORGE_TOKEN_ENCRYPTION_KEY`. Back it up: without it, stored tokens and upload sessions cannot be read.
