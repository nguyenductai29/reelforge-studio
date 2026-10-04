# Movie sources — manual verification

The automated suites cover movie sources with test doubles only: an in-memory Google Drive
(`tests/fake_drive.py`), mocked URLs, mocked AI providers, and a real FFmpeg run on a synthetic 40-second movie
(`tests/test_movie_sources.py`, `tests/test_movie_units.py`, `tests/test_movie_postgres.py`,
`e2e/tests/12-movie-sources.spec.ts`). Nothing there
proves that the production server, its Google Drive or a real provider works. The gates below are checked **by a
person on the production server** and recorded in Admin → Verification (group *Movie sources*), each right after it
was checked. They are **optional**: an installation that does not enable movie sources records them *Not
applicable*. No tool records them.

| Gate | Status |
| --- | --- |
| `movie_drive_connection` | MANUAL — not checked |
| `movie_import_local` | MANUAL — not checked |
| `movie_import_url` | MANUAL — not checked |
| `movie_scratch_download` | MANUAL — not checked |
| `movie_pipeline_live` (paid) | MANUAL — not checked |
| `movie_source_deletion` | MANUAL — not checked |
| `movie_retention_cleanup` | MANUAL — not checked |

What the gates cover, item by item:

| Check | Gate |
| --- | --- |
| Google Drive configured; the connection test | `movie_drive_connection` |
| A real upload to Drive (server file, direct URL) | `movie_import_local`, `movie_import_url` |
| A real download from Drive, once per source | `movie_scratch_download` |
| One short authorized movie through transcription, visual analysis, the review script, clip extraction and the final render | `movie_pipeline_live` |
| A real deletion; the source actually removed; deletion refused while in use | `movie_source_deletion` |
| The deletion scheduled by itself (retention or after success) and carried out | `movie_retention_cleanup` |

Use a short movie you are authorized to use (a few minutes is enough). Never paste a token, a key or a signed URL
into a note.

## Before

1. `./deploy.sh` brought the server to `0027_movie_sources`; `python -m alembic current` shows it.
2. FFmpeg and ffprobe: `python -m app.render_worker --check`.
3. Create the import folder, readable by the service account, on a disk with room for the largest movie twice:
   `sudo install -d -o tai -g tai -m 750 /srv/data/import/reelforge`.
4. Google Drive: a root folder in the Drive of the account (OAuth) or in a shared drive the service account belongs to;
   its ID from the folder's URL.
5. Admin → System settings → **Movie sources**: Google Drive (credentials, root folder ID, delete mode), then *Enable
   movie sources*; check the import folder and the scratch space.
6. `sudo systemctl enable --now reelforge-worker@movie`; `python -m app.movie_worker --check` says *Movie worker
   ready.*; Admin → Operations shows `movie_worker` reporting.

## Gates

### `movie_drive_connection`

Admin → System settings → Movie sources → *Test drive connection*: configuration, sign-in (the expected account),
root folder, test upload and test file deleted are all **OK**. In Google Drive, the root folder holds no
`reelforge-connection-test-*.txt` (not even in the trash when the deletion was permanent). On the server,
`python -m app.google_drive_check` prints *Drive ready.* Record the account shown (never a credential).

### `movie_import_local`

Copy the movie into the studio's import folder, `/srv/data/import/reelforge/<studio id>/` (the Add dialog names the
folder while it does not exist). As an editor: Media → Movie sources → *Add movie source* → *Server file* → the movie
→ confirm the rights → *Add and import*. A second studio's editor does not see that file. Within minutes it is **Ready**, with the right size, length and
resolution; the notification *Movie source ready* arrives; in Drive the file is
`<root>/movie-sources/<studio id>/<source id>/source.<ext>` with the same size. Then try a path outside the folder
with the API (`POST /api/movie-sources {"source_type":"local","path":"../x.mp4"}`): `422 invalid_path`.

### `movie_import_url`

*Direct URL* with an `https://` link to a movie file you may use: **Ready**; the detail shows the address without its
query string. Then `https://127.0.0.1/movie.mp4` is refused at once (`blocked_url`), and a link to an HTML page fails
the import with `unsupported_type`.

### `movie_scratch_download`

Start two reviews of the same ready source, one after the other (*Use for Movie Review* twice). The movie worker's
log (`journalctl -u reelforge-worker@movie`) shows `movie_scratch_downloaded` **once** for that source; the second
Prepare Movie reuses the copy.

### `movie_pipeline_live` (paid)

With the studio's default Text model able to read images (Gemini, OpenAI or Anthropic), a transcription model and a
voice model: *Use for Movie Review* on a ready source. The run shows each step with its counts; Visual Analysis
describes the frames without naming people from their faces; the Review Script's sections have time ranges; the
clips are short and from the right moments; Render produces an MP4 with narration and subtitles; approve the Review.
The credit history shows each paid step charged once (a refused batch refunded). Record the run ID.

### `movie_source_deletion`

While the review of the previous gate waits for review, *Delete now* on its source is refused (*A workflow run still
uses this movie source*). On a source no run uses, *Delete now*: **Deletion scheduled**, then **Deleted** within
minutes; the Drive file is in the trash (or gone with `delete_mode` `delete`); the scratch copy is gone; the final
video of the earlier run still plays.

### `movie_retention_cleanup`

Either wait for the grace period after the successful review of `movie_pipeline_live` (24 hours by default), or add a
source, set *Keep movie sources* to 1 day and wait for it to expire: the source becomes **Deleted** without anyone
pressing anything; Admin → Audit log shows `movie_source.delete_requested` (reason `after_success` or `expired`) then
`movie_source.deleted`.

## If something fails

* *drive_auth_failed*: the refresh token was revoked or the service account lost access; save new credentials, then
  *Retry* (upload) on the source.
* *drive_quota_exceeded*: Drive is full; free space or raise the account's storage, then retry.
* Deletions keep failing: the `movie_sources:deletion` alert names how many; the source's detail shows the code;
  *Try deleting now* retries at once.
* A source stuck in `importing`: the worker log shows the stage; a lease expires after 15 minutes and another
  attempt starts.

See [MOVIE_SOURCES.md](MOVIE_SOURCES.md) for how each part works.
