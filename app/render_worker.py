"""Render final videos with FFmpeg outside request transactions and save them as private MP4 assets.

Run ``python -m app.render_worker`` next to the API (``--once`` processes one
due job; ``--check`` reports FFmpeg, ffprobe and the subtitle font without
rendering). Each job renders one Render step: probe the clips, write the
re-timed subtitles, run one FFmpeg command in a private folder under
``<media>/.render-tmp/<job_id>``, check the MP4, move it into the workspace's
media and finish the step (``video_assets`` holds the one final video, marked
``final``). The folder is always removed.

The same worker cuts Movie Recap source clips (``clips.extract`` jobs of the
Extract Source Clips step, app/workflow/nodes/recap.py): each clip is cut from the
workspace's own source video with stream copy when the source is an MP4 and the
copy is a valid clip, otherwise re-encoded to H.264/AAC, and stored as an MP4
asset whose ``source_asset_id`` names the video it came from.

Rendering calls no paid provider, so every failure is final and clear: the step
fails with a stable code (``ffmpeg_missing``, ``input_missing``,
``font_unavailable``, ``render_failed``, ``render_timeout``, ``invalid_output``,
``storage_limit_exceeded``), a reserved price is refunded, and nothing needs
reconciliation. A job interrupted by a crash is rendered again when its lease
expires, at most ``MAX_ATTEMPTS`` times.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from sqlalchemy import select, update

from app import heartbeat
from app import jobs, render, storage, subtitles, usage
from app.db import Session
from app.logs import log_event
from app.media_paths import media_root
from app.models import Asset, UsageEvent, WorkflowJob, WorkflowRun, WorkflowRunStep
from app.provider_progress import step_output
from app.runtime_env import start_process
from app.video_files import valid_mp4
from app.workflow import ExecutionContext, NodeError, NodeExecutionResult, default_executor

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
TEMP_FOLDER = ".render-tmp"
RUNNING_DETAIL = "Đang render video."
DONE_DETAIL = "Đã render video hoàn chỉnh."
FAILED_DETAIL = "Render thất bại."
CLIPS_RUNNING_DETAIL = "Đang cắt clip từ video nguồn."
CLIPS_DONE_DETAIL = "Đã cắt clip từ video nguồn."
CLIPS_FAILED_DETAIL = "Cắt clip thất bại."
KINDS = ("render.generate", "clips.extract")
_ASSET_ID = re.compile(r"[A-Za-z0-9-]{1,64}\Z")


@dataclass
class Claim:
    job_id: str
    token: str
    payload: dict
    workspace_id: str
    run_id: str
    step_id: str
    workflow_id: str | None
    root: Path

    @property
    def fields(self) -> dict:
        return {"workspace_id": self.workspace_id, "workflow_id": self.workflow_id, "run_id": self.run_id,
                "step_id": self.step_id, "job_id": self.job_id}


def _now():
    return datetime.now(timezone.utc)


def _lock_run(db, run_id) -> WorkflowRun:
    # Same lock order as the executor, reconciliation and the other workers: run, then account.
    db.execute(update(WorkflowRun).where(WorkflowRun.id == run_id).values(status=WorkflowRun.status))
    return db.get(WorkflowRun, run_id)


def _live(db, claim: Claim):
    job = db.get(WorkflowJob, claim.job_id)
    if not job or job.state != "leased" or job.lease_token != claim.token or not job.lease_expires_at:
        return None
    expires = job.lease_expires_at if job.lease_expires_at.tzinfo else job.lease_expires_at.replace(tzinfo=timezone.utc)
    if expires <= _now():
        return None
    run = _lock_run(db, job.run_id)
    step = db.get(WorkflowRunStep, job.step_id)
    return (job, run, step) if step.status in ("queued", "running") else None


def _counts(payload) -> dict:
    return {"clip_count": len(payload.get("clips") or []), "audio_count": len(payload.get("audio") or []),
            "subtitle": bool(payload.get("subtitle"))}


def _fail(claim: Claim, error: render.RenderError, started: float) -> None:
    clips = claim.payload.get("kind") == "clips.extract"
    with Session.begin() as db:
        live = _live(db, claim)
        if not live or not jobs.fail_job(db, job_id=claim.job_id, lease_token=claim.token, error=error.code):
            return
        _, run, step = live
        credits = claim.payload.get("credits") or 0
        if credits and claim.payload.get("refund_reference"):
            # Rendering is local: a failure never needs reconciliation, so a reserved price is returned.
            usage.post_credit(db, claim.workspace_id, credits, "render_refund", claim.payload["refund_reference"])
            log_event(logger, "credit_refunded", **claim.fields, credits=credits,
                      reference=claim.payload["refund_reference"])
        output = step_output(step)
        output["render_error"] = str(error)[:300]
        default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()), step, NodeExecutionResult.failed(
            NodeError(error.code, str(error)[:300], False, error.category),
            CLIPS_FAILED_DETAIL if clips else FAILED_DETAIL, output=output))
    log_event(logger, "clips_failed" if clips else "render_failed", level=logging.WARNING, **claim.fields, **_counts(claim.payload),
              duration_ms=round((time.monotonic() - started) * 1000), error_code=error.code,
              error_category=error.category)


def _input(claim: Claim, asset_id) -> Path:
    if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
        raise render.RenderError("input_missing", "An input asset ID is invalid", "invalid_request")
    path = storage.file_in(claim.root, claim.workspace_id, asset_id)
    if not path.is_file():
        raise render.RenderError("input_missing", "An input file is missing from media storage", "invalid_request")
    return path


def _render(claim: Claim, folder: Path, runner) -> tuple[Path, dict, bool]:
    ffmpeg, ffprobe = render.tools()
    if not ffmpeg or not ffprobe:
        raise render.RenderError("ffmpeg_missing", "FFmpeg or ffprobe is not installed", "configuration_error")
    payload = claim.payload
    clip_paths = [(_input(claim, item.get("asset_id")), item.get("scene_index"), bool(item.get("still")))
                  for item in payload.get("clips") or []]
    tracks = [render.Track(_input(claim, item.get("asset_id")), item.get("scene_index"))
              for item in payload.get("audio") or []]
    if not clip_paths:
        raise render.RenderError("input_missing", "There are no clips to render", "invalid_request")
    folder.mkdir(parents=True, exist_ok=True)
    clips = []
    for path, scene_index, still in clip_paths:
        info = render.probe(ffprobe, path, runner, still=still)
        if info["duration"] <= 0 and not still:
            raise render.RenderError("render_failed", "A clip has no duration")
        clips.append(render.Clip(path, scene_index if isinstance(scene_index, int) else None, info["duration"],
                                 False if still else info["has_audio"], info["width"], info["height"], still))
    if any(clip.still for clip in clips):
        clips = _time_stills(clips, tracks, ffprobe, runner)
    music = payload.get("music") if isinstance(payload.get("music"), dict) else None
    music_track = render.Track(_input(claim, music.get("asset_id")), None) if music else None
    burn, style = False, None
    subtitle = payload.get("subtitle")
    if isinstance(subtitle, dict):
        if issue := render.font_issue(runner):
            raise render.RenderError(issue[0], issue[1], "configuration_error")
        cues = render.retime(subtitles.cues_from(subtitle.get("cues")), subtitle.get("segments") or [], clips)
        if cues:
            (folder / render.SUBTITLE_NAME).write_bytes(render.burn_in_file(cues))
            burn, style = True, render.force_style(subtitle.get("style"), render.subtitle_font())
    render.run_ffmpeg(render.build_command(ffmpeg, clips, tracks, subtitles=burn, style=style, music=music_track,
                                           music_volume=(music.get("volume") or 15) / 100 if music else 0.15,
                                           music_loop=not music or music.get("mode") != "once"),
                      folder, render.render_timeout_seconds(), runner)
    output = folder / render.OUTPUT_NAME
    if not output.is_file() or not valid_mp4(output, max_bytes=render.MAX_RENDER_BYTES):
        raise render.RenderError("invalid_output", "FFmpeg did not produce a valid MP4")
    info = render.probe(ffprobe, output, runner)
    size = render.frame_size(clips)
    return output, {"duration": round(info["duration"], 3), "width": info["width"] or size[0],
                    "height": info["height"] or size[1], "audio_policy": render.audio_mode(clips, tracks)}, burn


def _time_stills(clips, tracks, ffprobe, runner):
    """Give each still image its length: its scene's narration, else a share of one narration, else the default."""
    durations = {}
    for track in tracks:
        durations.setdefault(track.scene_index, 0.0)
        durations[track.scene_index] += max(0.0, render.probe(ffprobe, track.path, runner)["duration"])
    stills = [clip for clip in clips if clip.still]
    moving = sum(clip.duration for clip in clips if not clip.still)
    shared = sum(durations.values())
    # One narration for the whole video (no still has its own scene narration): the stills share what is left.
    sharing = shared > moving and not any(still.scene_index in durations for still in stills
                                          if still.scene_index is not None)
    default = render.still_seconds()
    timed = []
    for clip in clips:
        if not clip.still:
            timed.append(clip)
            continue
        if clip.scene_index is not None and durations.get(clip.scene_index):
            seconds = durations[clip.scene_index] + 0.3
        elif sharing:
            seconds = max(1.5, (shared - moving) / len(stills))
        else:
            seconds = default
        timed.append(render.Clip(clip.path, clip.scene_index, round(seconds, 3), False, clip.width, clip.height, True))
    return timed


def _store(claim: Claim, output: Path, facts: dict, burned: bool, started: float) -> None:
    asset_id = str(uuid.uuid4())
    filename = f"render-{asset_id[:8]}.mp4"
    size = output.stat().st_size
    target = storage.file_in(claim.root, claim.workspace_id, asset_id)
    moved = False
    try:
        with Session.begin() as db:
            live = _live(db, claim)
            if not live or not jobs.complete_job(db, job_id=claim.job_id, lease_token=claim.token):
                return  # another worker owns the job now
            _, run, step = live
            storage.lock_workspace(db, claim.workspace_id)
            if not storage.has_room(db, claim.workspace_id, size):
                raise render.RenderError("storage_limit_exceeded", "Workspace media quota reached", "configuration_error")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(output, target)
            moved = True
            db.add(Asset(id=asset_id, workspace_id=claim.workspace_id, project_id=run.project_id, run_id=run.id,
                         step_id=step.id, provider="ffmpeg", model="local", filename=filename,
                         content_type="video/mp4", bytes=size, kind=storage.FINAL_RENDER))
            credits = claim.payload.get("credits") or 0
            reference = claim.payload.get("usage_reference")
            if credits and reference and not db.scalar(select(UsageEvent.id).where(UsageEvent.reference == reference)):
                db.add(UsageEvent(id=str(uuid.uuid4()), workspace_id=claim.workspace_id, tool="ffmpeg/render",
                                  units=1, credits=credits, reference=reference, created_at=_now()))
            entry = {"id": asset_id, "asset_id": asset_id, "filename": filename, "content_type": "video/mp4",
                     "provider": "ffmpeg", "model": "local", "scene_index": None, "duration": facts["duration"],
                     "width": facts["width"], "height": facts["height"], "final": True}
            result = {**step_output(step), "video_assets": [entry], "asset_id": asset_id, "filename": filename,
                      "duration": facts["duration"], "width": facts["width"], "height": facts["height"],
                      "audio_policy": facts["audio_policy"], "subtitles_burned": burned}
            result.pop("render_error", None)
            db.flush()
            default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()), step,
                                         NodeExecutionResult.completed(DONE_DETAIL, result, asset_ids=(asset_id,)))
    except Exception:
        if moved:
            target.unlink(missing_ok=True)
        raise
    log_event(logger, "render_completed", **claim.fields, **_counts(claim.payload), asset_id=asset_id,
              duration_ms=round((time.monotonic() - started) * 1000), output_bytes=size,
              video_seconds=facts["duration"])


def _cut(claim: Claim, folder: Path, runner) -> list[tuple[Path, dict]]:
    """Each requested clip as a checked MP4 in ``folder`` with its facts, in request order."""
    ffmpeg, ffprobe = render.tools()
    if not ffmpeg or not ffprobe:
        raise render.RenderError("ffmpeg_missing", "FFmpeg or ffprobe is not installed", "configuration_error")
    requested = claim.payload.get("clips") or []
    if not requested or len(requested) > 20:
        raise render.RenderError("input_missing", "There are no clips to cut", "invalid_request")
    folder.mkdir(parents=True, exist_ok=True)
    cut = []
    for index, item in enumerate(requested, 1):
        source = _input(claim, item.get("source_asset_id"))
        start, end = item.get("start"), item.get("end")
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or isinstance(start, bool)
                or not 0 <= start < end or end - start > 120):
            raise render.RenderError("invalid_request", "A clip has invalid bounds", "invalid_request")
        name = f"clip-{index:02d}.mp4"
        output = folder / name
        attempts = ((True, False) if claim.payload.get("mode") != "reencode"
                    and item.get("content_type") == "video/mp4" else (False,))
        for copy in attempts:
            output.unlink(missing_ok=True)
            try:
                render.run_ffmpeg(render.clip_command(ffmpeg, source, start, end, name, copy=copy), folder,
                                  render.render_timeout_seconds(), runner)
                if not output.is_file() or not valid_mp4(output, max_bytes=render.MAX_RENDER_BYTES):
                    raise render.RenderError("invalid_output", "FFmpeg did not produce a valid MP4")
                info = render.probe(ffprobe, output, runner)
                if info["duration"] <= 0.1 or not info["width"]:
                    raise render.RenderError("invalid_output", "The clip has no video")
            except render.RenderError:
                if copy:
                    continue  # stream copy was not possible: re-encode instead
                raise
            cut.append((output, {"scene_index": item.get("scene_index"), "source_asset_id": item["source_asset_id"],
                                 "source_start": round(float(start), 3), "source_end": round(float(end), 3),
                                 "duration": round(info["duration"], 3), "width": info["width"],
                                 "height": info["height"], "cut": "copy" if copy else "reencode"}))
            break
    return cut


def _store_clips(claim: Claim, cut: list[tuple[Path, dict]], started: float) -> None:
    targets, entries = [], []
    total = sum(path.stat().st_size for path, _ in cut)
    try:
        with Session.begin() as db:
            live = _live(db, claim)
            if not live or not jobs.complete_job(db, job_id=claim.job_id, lease_token=claim.token):
                return  # another worker owns the job now
            _, run, step = live
            storage.lock_workspace(db, claim.workspace_id)
            if not storage.has_room(db, claim.workspace_id, total):
                raise render.RenderError("storage_limit_exceeded", "Workspace media quota reached", "configuration_error")
            folder = storage.file_in(claim.root, claim.workspace_id)
            folder.mkdir(parents=True, exist_ok=True)
            for path, facts in cut:
                asset_id = str(uuid.uuid4())
                filename = f"clip-{asset_id[:8]}.mp4"
                size = path.stat().st_size
                target = folder / asset_id
                os.replace(path, target)
                targets.append(target)
                db.add(Asset(id=asset_id, workspace_id=claim.workspace_id, project_id=run.project_id, run_id=run.id,
                             step_id=step.id, provider="ffmpeg", model="local", filename=filename,
                             content_type="video/mp4", bytes=size, source_asset_id=facts["source_asset_id"],
                             kind=storage.EXTRACTED_CLIP))
                entries.append({"id": asset_id, "asset_id": asset_id, "filename": filename, "content_type": "video/mp4",
                                "provider": "ffmpeg", "model": "local", **facts})
            entries.sort(key=lambda entry: entry["scene_index"] if isinstance(entry["scene_index"], int) else 0)
            result = {**step_output(step), "video_assets": entries, "clip_count": len(entries)}
            result.pop("render_error", None)
            db.flush()
            default_executor.finish_step(ExecutionContext.for_run(db, run, now=_now()), step,
                                         NodeExecutionResult.completed(CLIPS_DONE_DETAIL, result,
                                                                       asset_ids=tuple(entry["id"] for entry in entries)))
    except Exception:
        for target in targets:
            target.unlink(missing_ok=True)
        raise
    log_event(logger, "clips_extracted", **claim.fields, clip_count=len(entries), output_bytes=total,
              copied=sum(1 for entry in entries if entry["cut"] == "copy"),
              duration_ms=round((time.monotonic() - started) * 1000))


def run_one(*, runner=subprocess.run, worker_id: str | None = None) -> bool:
    """Claim and render one due job; returns False when none is due."""
    timeout = render.render_timeout_seconds()
    worker_id = worker_id or f"render-{os.getpid()}"
    with Session.begin() as db:
        claimed = jobs.claim_due_jobs(db, worker_id=worker_id, limit=1, lease_seconds=timeout + 300,
                                      logical_key_prefix="render:")
        if not claimed:
            return False
        job = claimed[0]
        run = _lock_run(db, job.run_id)
        step = db.get(WorkflowRunStep, job.step_id)
        claim = Claim(job.id, job.lease_token, job.payload, job.workspace_id, job.run_id, job.step_id,
                      run.workflow_id if run else None, media_root(db))
        log_event(logger, "job_claimed", **claim.fields, worker_id=worker_id, attempt=job.attempt_count)
        kind = job.payload.get("kind")
        if step.status not in ("queued", "running") or kind not in KINDS:
            jobs.fail_job(db, job_id=job.id, lease_token=job.lease_token, error="invalid_step_state")
            return True
        exhausted = job.attempt_count > MAX_ATTEMPTS
        if not exhausted:
            step.status, step.detail = "running", CLIPS_RUNNING_DETAIL if kind == "clips.extract" else RUNNING_DETAIL
    started = time.monotonic()
    if exhausted:
        _fail(claim, render.RenderError("attempts_exhausted", "The render was interrupted too many times"), started)
        return True
    folder = claim.root / TEMP_FOLDER / claim.job_id
    try:
        if kind == "clips.extract":
            log_event(logger, "clips_started", **claim.fields, clip_count=len(claim.payload.get("clips") or []))
            _store_clips(claim, _cut(claim, folder, runner), started)
        else:
            log_event(logger, "render_started", **claim.fields, **_counts(claim.payload))
            output, facts, burned = _render(claim, folder, runner)
            _store(claim, output, facts, burned, started)
    except render.RenderError as exc:
        _fail(claim, exc, started)
    except Exception as exc:  # noqa: BLE001 - reported as a worker error, never retried silently
        logger.exception("render worker error")
        _fail(claim, render.RenderError("worker_error", f"Render worker error: {type(exc).__name__}"), started)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return True


def check(out=print) -> int:
    """Report the render tools and the subtitle font; nothing is rendered."""
    ffmpeg, ffprobe = render.tools()
    out(f"ffmpeg: {ffmpeg or 'missing'}")
    out(f"ffprobe: {ffprobe or 'missing'}")
    issue = render.tools_issue() or render.font_issue()
    try:
        out(f"Subtitle font: {render.subtitle_font()}")
    except render.RenderError as exc:
        out(f"Subtitle font: invalid ({exc})")
    out("Render ready." if not issue else f"Not ready: {issue[1]}")
    return 0 if not issue else 1


def main():
    parser = argparse.ArgumentParser(description="Process ReelForge render (FFmpeg) jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one due job")
    parser.add_argument("--check", action="store_true", help="Report FFmpeg, ffprobe and the subtitle font, then exit")
    args = parser.parse_args()
    start_process("render_worker")
    if args.check:
        sys.exit(check())
    while True:
        worked = run_one()
        heartbeat.beat("render_worker")
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
