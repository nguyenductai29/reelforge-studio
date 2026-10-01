# Storage lifecycle: quotas, retention and cleanup (Phase 17)

ReelForge keeps media on the local disk of the server; no cloud storage is required. This page explains:

- where files go;
- how much each studio may store;
- what is deleted, when, and how safely;
- how to set up a home server with an SSD and an HDD.

Code: `app/storage.py` (paths, quotas, kinds, eligibility) and `app/media_maintenance.py` (safe deletion and the daily command). Migration: `0016_storage_lifecycle`.

## Storage root and layout

ReelForge uses **one** storage root:

1. `REELFORGE_STORAGE_ROOT` (environment, shared by the API and every worker), if set;
2. else the `storage_dir` system setting (default `instance/media`).

A relative path is resolved under the project folder. Settings → Storage shows the folder in use to system admins only, and whether it comes from the environment.

```
<root>/
  <workspace_id>/
    <asset_id>          every file, named by IDs only
    <asset_id>.part     a file still being written
  .render-tmp/<job>/    render and clip-extraction scratch space
  .source-tmp/<job>/    transcription scratch space
  .publish-tmp/<job>.mp4  the upload copy
```

- **Only IDs in paths.** File and project names are user input, so paths never contain them. Every path is built through `storage.file_in`/`asset_path`, which refuse any ID containing a separator or a dot.
- **Projects are not folders.** The project is a column of the asset (`assets.project_id`), so attaching an upload to another project moves no file. The project, run, step and source lineage stays in the database.
- **No absolute path reaches a user.** API responses carry asset IDs. Only the admin Settings tab shows the root.

**Why one root?** A single root keeps quota, lineage and cleanup in one place, and it moves with one `mv`. Splitting images, uploads and videos across `/srv/data/images`, `/srv/data/uploads` and `/srv/data/videos` would spread every check over three trees without a real benefit.

## Quota

Each plan has a **storage limit** (`plans.storage_limit_bytes`). Admin → Plans edits it in GB.

| Plan | Default after migration 0016 |
| --- | --- |
| Trial | 1 GB |
| Standard | 10 GB |
| Pro | 30 GB |

- A plan with no limit uses `WORKSPACE_MEDIA_QUOTA_BYTES`, or 1 GiB when that is not set.
- When `WORKSPACE_MEDIA_QUOTA_BYTES` is set, it also **caps** every plan. Use it as a disk-safety ceiling.
- **Usage** is the sum of the studio's stored asset bytes. Uploads, generated images, scene videos, narration, subtitles, render outputs and extracted source clips all count. A removed asset counts 0.
- **Enforcement** applies before anything is stored:
  - **Uploads:** refused with 413 at 100 %, and when the file would overflow the quota.
  - **Image, voice and video jobs:** refused before the paid provider call; the credits are refunded.
  - **Render, Extract Source Clips and Subtitle:** blocked with `storage_limit_exceeded`.
- **No race.** Every check that stores media first takes the studio's row lock (`storage.lock_workspace`), in the same transaction that records the asset. Two uploads, or an upload and a worker, therefore cannot both use the same remaining room. `tests/test_storage_lifecycle.py` runs two concurrent uploads that together exceed the quota: exactly one succeeds.

### Warning levels

| Usage | Level | What the studio sees |
| --- | --- | --- |
| < 70 % | `ok` | Usage bar |
| ≥ 70 % | `notice` | Note under the bar |
| ≥ 80 % | `warning` | Banner on every page, linking to Settings → Storage |
| ≥ 90 % | `critical` | Red banner: new media will soon be blocked |
| ≥ 100 % | `full` | Uploads and new media are blocked |

At every level, existing media can still be viewed, downloaded and published.

Admins see:

- the level of every studio (Admin → Studios & credits, storage column);
- the number of studios at each level, the fullest studios and the free space of the media disk (Admin → Operations);
- a header count of studios at 90 % or more.

## Kinds and retention

Each asset has a `kind`. It is set where the asset is created. Migration 0016 labelled older assets from the step that produced them; it never infers the kind from a file name.

| Kind | Made by | Retention |
| --- | --- | --- |
| `source` | an upload | **kept** until the user deletes it |
| `final_render` | Render | **kept** |
| `subtitle` | Subtitle | kept (a few KB) |
| `generated_image` | Image | intermediate: 30 days |
| `scene_video` | Video | intermediate: 30 days |
| `voice` | Voice | intermediate: 30 days |
| `extracted_clip` | Extract Source Clips | intermediate: 30 days |
| `other` | anything unclassified | kept |

An intermediate asset expires only when all of these hold:

1. it is older than `REELFORGE_RETENTION_INTERMEDIATE_DAYS` (default 30; `0` keeps intermediates forever);
2. **its run has a final render** that is still stored. The render already contains the intermediate, so the final MP4 never depends on it. A run without a render keeps its clips, because a clip may be the deliverable (the Social video template publishes the clip itself);
3. **no publication refers to it.**

File leftovers have their own ages:

| Leftover | Default | Variable |
| --- | --- | --- |
| `.part` files of interrupted writes | 1 day | `REELFORGE_RETENTION_PARTIAL_DAYS` |
| Worker scratch folders | 3 days | `REELFORGE_RETENTION_TEMP_DAYS` |
| Orphan files without an asset row (only with `--orphans`) | 3 days | `REELFORGE_RETENTION_ORPHAN_DAYS` |

Nothing younger than 24 hours is ever touched.

**Not deleted automatically:** final videos, original uploads, subtitles, and anything a publication uses. There is no automatic deletion of sources. When a studio is full, it is told so and frees space itself.

## What "expired" means

An expired or deleted asset **keeps its row** (lineage, run history and publications still resolve) with:

- `bytes = 0`;
- `expired_at`;
- `expired_reason` (`retention`, `cleanup` or `deleted`);
- `expired_bytes`, the size that was freed.

Its file is removed. Downloading it answers **410** `{"code": "media_expired", "reason": …}`, and the interface shows "File no longer available" instead of a broken frame.

Lists, quotas and publishing ignore it:

- `GET /api/dashboard` (Library and Media) lists only stored assets;
- publishing already requires `bytes > 0`;
- re-rendering a run whose intermediates expired is blocked with "input file missing". The final video is unaffected.

## Daily cleanup

```bash
python -m app.media_maintenance                                # dry run (default; same as --dry-run)
python -m app.media_maintenance --apply --intermediates         # the daily job
python -m app.media_maintenance --apply --intermediates --orphans
python -m app.media_maintenance --usage                         # bytes, quota, percent and level per studio
```

| Option | Default | Effect |
| --- | --- | --- |
| *(no option)* | — | Old `.part` files and worker scratch folders, by the policy ages |
| `--intermediates` | off | Also expires intermediate assets past retention (reads and writes the asset table) |
| `--orphans` | off | Also removes files that have no asset row |
| `--older-than-hours N` | — | One age (at least 24) for partials, scratch folders and orphans instead of the policy |

The job is a dry run unless `--apply` is given.

### Safe deletion

For each intermediate asset:

1. **Path check first.** The file must be exactly `<root>/<workspace_id>/<asset_id>`, with both IDs safe, and be a regular file. Neither it, its workspace folder nor any ancestor of the root may be a symbolic link or junction, and it must resolve inside the root. Otherwise the asset is skipped and left untouched, and the job reports it.
2. **Eligibility again.** The job takes the studio lock and re-checks the asset under a row lock (`SELECT … FOR UPDATE`): still eligible, still not used by a publication.
3. **Mark it expired**, then commit.
4. **Remove the file** with the same path check.

If the process stops between steps 3 and 4, the next run finds the expired row whose file still exists and removes the file (the "files of earlier expiries removed" count). No arbitrary path is ever deleted, and the job never follows a link.

### Schedule it daily at 03:00 (systemd)

`/etc/systemd/system/reelforge-media-maintenance.service`:

```ini
[Unit]
Description=ReelForge Studio daily media maintenance
After=network-online.target

[Service]
Type=oneshot
User=tai
Group=tai
WorkingDirectory=/home/tai/apps/reelforge-studio
ExecStart=/home/tai/apps/reelforge-studio/.venv/bin/python -m app.media_maintenance --apply --intermediates
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/etc/reelforge/runtime.env
Nice=10
IOSchedulingClass=idle
```

`/etc/systemd/system/reelforge-media-maintenance.timer`:

```ini
[Unit]
Description=Run ReelForge media maintenance daily

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now reelforge-media-maintenance.timer
systemctl list-timers reelforge-media-maintenance.timer
sudo -u tai /home/tai/apps/reelforge-studio/.venv/bin/python -m app.media_maintenance --intermediates   # preview first
journalctl -u reelforge-media-maintenance
```

With cron instead (`crontab -e` as `tai`; cron does not read the runtime file, so load it):

```cron
0 3 * * * cd /home/tai/apps/reelforge-studio && set -a && . /etc/reelforge/runtime.env && set +a && .venv/bin/python -m app.media_maintenance --apply --intermediates >> /home/tai/reelforge-maintenance.log 2>&1
```

## What users can delete themselves

These actions are for the workspace owner, and each one asks for confirmation.

| Where | Action | API |
| --- | --- | --- |
| Settings → Storage | **Delete intermediate media** of all projects or one project. Lists what would go first, then removes it whatever its age (same rules: final render exists, no publication) | `POST /api/storage/cleanup {project_id?, apply}` (`apply` false previews) |
| Media | **Delete** the open file, or select several (filter by project, "select all") and delete them | `DELETE /api/assets/{id}`, `POST /api/assets/delete {asset_ids}` |

Media that a publication still needs is skipped with `asset_in_use`: any publication that has not succeeded or been cancelled (scheduled, queued, uploading, failed and retryable, needing attention). Another studio's media answers 404. Deleting a source keeps the clips that were cut from it.

## API summary

| Endpoint | Returns |
| --- | --- |
| `GET /api/storage` | `used_bytes`, `quota_bytes`, `percent`, `level`, `by_type`, `intermediate {assets, bytes}`, `retention` |
| `GET /api/dashboard` | `storage` (`used_bytes`, `quota_bytes`, `percent`, `level`) |
| `GET /api/admin/storage?limit&offset` | Studios fullest first (with level, quota and files), `levels` counts, `disk {total_bytes, used_bytes, free_bytes, percent}` (never its path), `retention` |
| `GET /api/admin`, `GET /api/admin/workspaces` | `counts.storage_alerts`, `storage_levels`, and `storage` per studio |
| `PUT /api/admin/plans/{code}` | Accepts `storage_limit_bytes` (64 MiB – 100 TiB, or null). Left out, the limit is unchanged |

## Home-server layout (SSD + HDD)

| Disk | Holds |
| --- | --- |
| **SSD 256 GB** | OS, application code and virtualenv, Node build, PostgreSQL data, Docker/system files |
| **HDD 1 TB, mounted at `/srv/data`** | Media and backups |

```
/srv/data/
├── backups/
│   └── reelforge/        PostgreSQL dumps (pg_dump)
├── images/               other projects
├── uploads/              other projects
└── videos/
    └── reelforge/        REELFORGE_STORAGE_ROOT: every ReelForge media file
```

```bash
sudo mkdir -p /srv/data/videos/reelforge /srv/data/backups/reelforge
sudo chown -R tai:tai /srv/data/videos/reelforge /srv/data/backups/reelforge
echo 'REELFORGE_STORAGE_ROOT=/srv/data/videos/reelforge' | sudo tee -a /etc/reelforge/runtime.env
```

`/srv/data/images` and `/srv/data/uploads` stay free for other projects. ReelForge keeps all of its media (uploads, images, voice, videos) under one root, for the reasons above. Mount the HDD itself at `/srv/data`; do not make the root a symbolic link, because cleanup refuses to delete through links.

**Moving an existing installation:**

1. Stop the API and every worker.
2. Run `rsync -a <old root>/ /srv/data/videos/reelforge/`.
3. Set `REELFORGE_STORAGE_ROOT`.
4. Start the services.
5. Check that a few media files open, then remove the old copy.

### Backups

- PostgreSQL metadata is small next to the video files: megabytes against hundreds of gigabytes. Back it up often, for example with a daily `pg_dump -Fc` into `/srv/data/backups/reelforge/`.
- **A backup on the same HDD does not protect against that disk failing.** Copy the database dumps to another machine or disk as well, and keep `REELFORGE_TOKEN_ENCRYPTION_KEY` with them.
- Do not copy the full video tree onto the same HDD by default: it doubles the space and gives no protection against disk failure. If the videos matter, back them up to a second disk or another host with `rsync`. Restore the database and the media from the same point in time; files without rows can be listed with `--orphans`.

## Limits

- One root only; there are no per-category roots.
- Intermediate retention is a single number of days for all intermediate kinds.
- Sources are never removed automatically. A full studio must delete media itself, or an admin raises its plan's limit.
- Disk space is shown, not enforced: plan limits that add up to more than the disk can still fill it. Keep `WORKSPACE_MEDIA_QUOTA_BYTES` and the plan limits within the HDD's capacity.
- Re-rendering a run after its intermediates expired is not possible; start a new run.
