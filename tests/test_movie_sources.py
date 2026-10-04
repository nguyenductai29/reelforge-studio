"""Movie sources end to end on a disposable database: settings, imports (server file, direct URL, Drive inbox), the
Drive upload, failures and retries, permissions, workspace isolation, retention, deletion and active-use protection,
then the movie steps of a review (prepare, frame analysis, clips) with every provider and FFmpeg faked.

Google Drive is tests/fake_drive.py behind ``httpx.MockTransport``; URLs are served by a mock transport with a
resolver that answers a public address. Nothing leaves the process and no real key is used."""
import os
from pathlib import Path
import shutil
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from studio_harness import run_program  # noqa: E402


def fake_drive_source() -> str:
    """tests/fake_drive.py as code for a harness program (which copies only app/ and migrations/)."""
    text = (HERE / "fake_drive.py").read_text(encoding="utf-8").split('\nif __name__ == "__main__":', 1)[0]
    return text.replace("from __future__ import annotations\n", "")


COMMON = r'''
import hashlib, socket, time, uuid
from app import alerts, google_drive, movie_sources, movie_worker
from app.models import AuditEvent, Membership, MovieSource, MovieSourceUse, Notification, User, WorkerHeartbeat

IMPORT = Path("instance/import-root").resolve()
STUDIO_IMPORT = IMPORT / workspace  # one import folder per studio
(STUDIO_IMPORT / "films").mkdir(parents=True)
MOVIE = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + os.urandom(300_000)
(STUDIO_IMPORT / "films" / "movie.mp4").write_bytes(MOVIE)
(STUDIO_IMPORT / "films" / "second.mp4").write_bytes(MOVIE[:200_000])
(STUDIO_IMPORT / "notes.txt").write_text("not a movie")
(IMPORT / "elsewhere.mp4").write_bytes(MOVIE)  # in the root itself: no studio can pick it
drive = FakeDrive()
TRANSPORT = httpx.MockTransport(drive.handle)
RealClient = google_drive.DriveClient

class FakeDriveClient(RealClient):
    """The API and the worker build their own Drive clients: every one of them talks to the fake Drive."""
    def __init__(self, settings=None, *, http_client=None, clock=time.time):
        super().__init__(settings, http_client=http_client or httpx.Client(transport=TRANSPORT), clock=clock)

google_drive.DriveClient = FakeDriveClient
DRIVE_SECRETS = ("drive-client-SECRET", "drive-refresh-SECRET", "drive-access-token-SENTINEL")
seen_text = []

def call(method, url, **kwargs):
    response = getattr(client, method)(url, **kwargs)
    seen_text.append(response.text)
    return response

PROBE = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "125.0"},
         "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
                     {"codec_type": "audio", "codec_name": "aac"}]}
broken_probe = set()

def probe_runner(args, **kwargs):
    assert Path(args[0]).name == "ffprobe", args
    if broken_probe:  # while set, ffprobe cannot read anything
        return subprocess.CompletedProcess(args, 1, "", "moov atom not found")
    return subprocess.CompletedProcess(args, 0, json.dumps(PROBE), "")

def resolver(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

def media(request):
    path = request.url.path
    if path == "/films/movie.mp4":
        return httpx.Response(200, headers={"Content-Type": "video/mp4", "Content-Length": str(len(MOVIE))}, content=MOVIE)
    if path == "/redirect.mp4":
        return httpx.Response(302, headers={"Location": "https://10.0.0.8/secret.mp4"})
    if path == "/page.mp4":
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>login</html>")
    if path == "/random.mp4":
        return httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=os.urandom(5000))
    if path == "/big.mp4":
        return httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=MOVIE[:32] + os.urandom(2 * 1024 * 1024))
    return httpx.Response(404)

HTTP = httpx.Client(transport=httpx.MockTransport(media))

def work(times=1):
    """Run the movie worker's source lane ``times`` times."""
    for _ in range(times):
        assert movie_worker.run_source_work(session_factory=Session, http_client=HTTP, resolver=resolver,
                                            runner=probe_runner, worker_id="test-worker")

def source(source_id):
    with Session() as db:
        return db.get(MovieSource, source_id)

def view(source_id):
    response = call("get", f"/api/movie-sources/{source_id}")
    assert response.status_code == 200, response.text
    return response.json()

def add(**body):
    return call("post", "/api/movie-sources", json=body)

def ready(**body):
    response = add(**body)
    assert response.status_code == 201, response.text
    created = response.json()
    work(2)
    current = view(created["id"])
    assert current["status"] == "ready", current
    return current

def actions(target_id):
    with Session() as db:
        return [row.action for row in db.scalars(select(AuditEvent).where(AuditEvent.target_id == target_id)
                                                 .order_by(AuditEvent.created_at, AuditEvent.id))]

def set_role(role):
    with Session.begin() as db:
        owner = db.scalar(select(User.id).where(User.email == "owner@example.com"))
        db.execute(update(Membership).where(Membership.user_id == owner, Membership.workspace_id == workspace)
                   .values(role=role))

def configure(**values):
    response = call("put", "/api/admin/system-config/movie_sources", json={"values": values})
    assert response.status_code == 200, response.text
    return response.json()
'''


SOURCES = r'''
# Off by default: nothing can be added until the system admin enables movie sources and Google Drive.
assert call("get", "/api/movie-sources/config").json()["enabled"] is False
assert add(source_type="local", path="films/movie.mp4").json()["detail"]["code"] == "movie_sources_disabled"
saved = call("put", "/api/admin/system-config/movie_sources", json={
    "values": {"movie_sources.enabled": True, "movie_sources.local_import_root": str(IMPORT),
               "movie_sources.drive.enabled": True, "movie_sources.drive.root_folder_id": "rootfolder01",
               "movie_sources.drive.client_id": "client-id.apps.googleusercontent.com"},
    "secrets": {"movie_sources.drive.client_secret": {"action": "replace", "value": "drive-client-SECRET"},
                "movie_sources.drive.refresh_token": {"action": "replace", "value": "drive-refresh-SECRET"}}})
assert saved.status_code == 200, saved.text
overview = call("get", "/api/admin/system-config").json()
assert overview["movie_sources"]["drive_problem"] is None
keys = {item["key"]: item for item in overview["sections"]["movie_sources"]}
assert keys["movie_sources.drive.refresh_token"]["configured"] is True

# Test connection: credentials, root folder, upload and permanent deletion; no test file is left behind.
tested = call("post", "/api/admin/system-config/movie_sources/drive/test")
assert tested.status_code == 200 and tested.json()["status"] == "ok", tested.text
assert drive.live_files() == [] and [item for item in drive.files.values() if item["mimeType"] != FOLDER] == []
history = call("get", "/api/admin/system-config").json()["movie_sources"]["last_test"]
assert history["action"] == "tested", history
# The same test from the server's shell: python -m app.google_drive_check (never prints a credential).
from app import google_drive_check
printed = []
assert google_drive_check.main([], out=printed.append) == 0, printed
assert printed[-1] == "Drive ready." and any("drive-owner@example.com" in line for line in printed), printed
assert google_drive_check.main(["--config", "--json"], out=printed.append) == 0
assert json.loads(printed[-1])["status"] == "ok"
assert not any(secret in line for line in printed for secret in DRIVE_SECRETS)
assert drive.live_files() == []

config = call("get", "/api/movie-sources/config").json()
assert config["enabled"] and config["local_import"] and config["drive_problem"] is None and config["retention_days"] == 7

# The import folder browser: relative names only, nothing outside the folder.
top = call("get", "/api/movie-sources/local-files").json()
assert [entry["name"] for entry in top["entries"]] == ["films"], top
films = call("get", "/api/movie-sources/local-files", params={"folder": "films"}).json()
assert [entry["name"] for entry in films["entries"]] == ["movie.mp4", "second.mp4"]
assert str(IMPORT) not in json.dumps(films)
assert call("get", "/api/movie-sources/local-files", params={"folder": "../"}).json()["detail"]["code"] == "invalid_path"

# Server files: only regular movie files inside the import folder.
assert add(source_type="local", path="../outside.mp4").json()["detail"]["code"] == "invalid_path"
assert add(source_type="local", path="../elsewhere.mp4").json()["detail"]["code"] == "invalid_path"
assert add(source_type="local", path="/etc/passwd").json()["detail"]["code"] == "invalid_path"
assert add(source_type="local", path="notes.txt").json()["detail"]["code"] == "unsupported_type"
assert add(source_type="local", path="films/missing.mp4").json()["detail"]["code"] == "not_found"
assert add(source_type="url", url="http://media.example.com/films/movie.mp4").json()["detail"]["code"] == "blocked_url"
assert add(source_type="url", url="https://127.0.0.1/movie.mp4").json()["detail"]["code"] == "blocked_url"
assert add(source_type="drive", drive_file_id="../x").json()["detail"]["code"] == "invalid_drive_file"
assert add(source_type="ftp", path="x").status_code == 422

created = add(source_type="local", path="films/movie.mp4", project_id=project)
assert created.status_code == 201, created.text
local = created.json()
assert local["status"] == "importing" and local["local_path"] == "films/movie.mp4" and local["name"] == "movie.mp4"
assert str(IMPORT) not in created.text and "drive_file_id" not in local
work()
assert view(local["id"])["status"] == "uploading" and source(local["id"]).checksum_md5 == hashlib.md5(MOVIE).hexdigest()
work()
local = view(local["id"])
assert local["status"] == "ready" and local["duration_seconds"] == 125.0 and local["width"] == 1280, local
assert local["bytes"] == len(MOVIE) and local["stored_in_drive"] and local["can_use"] and not local["in_use"]
row = source(local["id"])
folder = drive.folder("movie-sources", workspace, local["id"])
assert folder and row.drive_folder_id == folder
stored = drive.files[row.drive_file_id]
assert stored["name"] == "source.mp4" and stored["parents"] == [folder] and drive.content[row.drive_file_id] == MOVIE
assert not movie_sources.import_dir(movie_sources.scratch_root(Session()), local["id"]).exists()  # local temp removed
assert actions(local["id"]) == ["movie_source.created", "movie_source.import_started", "movie_source.ready"]
with Session() as db:
    note = db.scalar(select(Notification).where(Notification.type == "movie_source.ready"))
    assert note is not None and note.link == f"/media/movie-sources?source={local['id']}"

# A direct URL: fetched once from the checked address; only its display form is kept.
url_source = ready(source_type="url", url="https://media.example.com/films/movie.mp4?sig=abc&token=xyz")
assert url_source["original_url"] == "https://media.example.com/films/movie.mp4", url_source
assert source(url_source["id"]).url_ciphertext is None  # the full address is gone once the movie is ready
assert "sig=abc" not in json.dumps(url_source)

def failed(body, code):
    response = add(**body)
    assert response.status_code == 201, response.text
    work()
    current = view(response.json()["id"])
    assert current["status"] == "failed" and current["failure"]["code"] == code, current
    assert current["failure"]["stage"] == "import"
    return current

redirected = failed({"source_type": "url", "url": "https://media.example.com/redirect.mp4"}, "blocked_url")
assert actions(redirected["id"])[-1] == "movie_source.import_failed"
failed({"source_type": "url", "url": "https://media.example.com/page.mp4"}, "unsupported_type")
failed({"source_type": "url", "url": "https://media.example.com/random.mp4"}, "unsupported_type")
configure(**{"movie_sources.max_source_bytes": 1024 * 1024})
failed({"source_type": "url", "url": "https://media.example.com/big.mp4"}, "too_large")
call("put", "/api/admin/system-config/movie_sources", json={"reset": ["movie_sources.max_source_bytes"]})
broken_probe.add("imports")  # ffprobe cannot read it
probe_fail = add(source_type="local", path="films/second.mp4")
work()
broken_probe.clear()
assert view(probe_fail.json()["id"])["failure"]["code"] == "invalid_media"
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.type == "movie_source.failed")) >= 5
assert not any(path.name.endswith(".part") for path in (movie_sources.scratch_root(Session()) / "imports").rglob("*"))

# The Drive inbox: only files placed in this studio's own inbox; the file is moved, never copied.
inbox = drive.folder("inbox", workspace, create=True)
placed = drive.add_file(inbox, "Phim tài liệu.mp4", MOVIE)
listing = call("get", "/api/movie-sources/drive-files").json()
assert [item["name"] for item in listing["files"]] == ["Phim tài liệu.mp4"] and listing["files"][0]["bytes"] == len(MOVIE)
elsewhere = drive.add_file(drive.root_id, "not-in-inbox.mp4", MOVIE)
assert failed({"source_type": "drive", "drive_file_id": elsewhere, "name": "x"}, "invalid_drive_file")
from_drive = ready(source_type="drive", drive_file_id=placed, name="Phim tài liệu.mp4")
assert source(from_drive["id"]).drive_file_id == placed and drive.files[placed]["parents"] == [
    drive.folder("movie-sources", workspace, from_drive["id"])] and drive.files[placed]["name"] == "source.mp4"
assert drive.children(inbox) == []

# Drive refuses the credentials during the upload: failed at the upload stage, retried once fixed.
refused = add(source_type="local", path="films/movie.mp4")
work()
drive.fail("token", 401)
work()
current = view(refused.json()["id"])
assert current["status"] == "failed" and current["failure"] == {"stage": "upload", "code": "drive_auth_failed",
                                                                "message": current["failure"]["message"]}, current
assert current["can_retry_upload"] and actions(current["id"])[-1] == "movie_source.drive_failed"
# Admin → Overview: today's failed imports; a warning while Google Drive refuses uploads.
attention = {item["key"]: item for item in call("get", "/api/admin/overview").json()["attention"]}
assert attention["movie_sources_failed"]["severity"] == "warning" and attention["movie_sources_failed"]["drive"] == 1
assert attention["movie_sources_failed"]["count"] >= 6 and attention["movie_sources_failed"]["tab"] == "system"
retried = call("post", f"/api/movie-sources/{current['id']}/retry", json={"stage": "upload"})
assert retried.status_code == 200 and retried.json()["status"] == "uploading", retried.text
work()
assert view(current["id"])["status"] == "ready"
attention = {item["key"]: item for item in call("get", "/api/admin/overview").json()["attention"]}
assert attention["movie_sources_failed"]["severity"] == "info"  # the member's own links and files only

# A transient Drive error resumes the same upload session later instead of starting again.
flaky = add(source_type="local", path="films/movie.mp4").json()
work()
starts = sum(1 for method, path in drive.calls if method == "POST" and path == "/upload/drive/v3/files")
with unittest_patch(google_drive, "CHUNK", 256 * 1024):
    drive.fail("upload_chunk", 503)
    work()
    row = source(flaky["id"])
    assert row.status == "uploading" and row.failure_code == "drive_server_error" and row.upload_session_ciphertext
    assert row.next_attempt_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    work_due = movie_worker.run_source_work(session_factory=Session, runner=probe_runner)
    assert work_due is False  # not due yet
    with Session.begin() as db:
        db.get(MovieSource, flaky["id"]).next_attempt_at = datetime.now(timezone.utc)
    work()
assert view(flaky["id"])["status"] == "ready"
assert sum(1 for method, path in drive.calls if method == "POST" and path == "/upload/drive/v3/files") == starts + 1

# Roles: viewers read; editors add, extend and start reviews; only owners and admins delete.
set_role("viewer")
assert call("get", "/api/movie-sources").status_code == 200
assert add(source_type="local", path="films/movie.mp4").status_code == 403
assert call("post", f"/api/movie-sources/{local['id']}/extend", json={"days": 1}).status_code == 403
assert call("delete", f"/api/movie-sources/{local['id']}").status_code == 403
assert call("post", f"/api/movie-sources/{local['id']}/movie-review", json={}).status_code == 403
set_role("editor")
assert call("post", f"/api/movie-sources/{local['id']}/extend", json={"days": 1}).status_code == 200
assert call("delete", f"/api/movie-sources/{local['id']}").status_code == 403
set_role("owner")

# Retention: +1, +3 or +7 days, never past the maximum.
before = datetime.fromisoformat(view(local["id"])["expires_at"])
extended = call("post", f"/api/movie-sources/{local['id']}/extend", json={"days": 3}).json()
assert datetime.fromisoformat(extended["expires_at"]) - before == timedelta(days=3)
assert call("post", f"/api/movie-sources/{local['id']}/extend", json={"days": 2}).status_code == 422
codes = [call("post", f"/api/movie-sources/{local['id']}/extend", json={"days": 7}).status_code for _ in range(5)]
assert codes[-1] == 409 and 200 in codes, codes
assert call("post", f"/api/movie-sources/{local['id']}/extend", json={"days": 7}).json()["detail"]["code"] == "retention_limit"
limit = datetime.now(timezone.utc) + timedelta(days=30)
assert abs((datetime.fromisoformat(view(local["id"])["expires_at"]) - limit).total_seconds()) < 120
assert actions(local["id"]).count("movie_source.retention_extended") >= 3

# Another studio sees none of it.
listed = call("get", "/api/movie-sources").json()
assert listed["total"] >= 5 and all("drive_file_id" not in item for item in listed["items"])
other = call("post", "/api/workspaces", json={"name": "Studio hai"})
assert other.status_code == 201, other.text
assert call("get", "/api/movie-sources").json()["total"] == 0
# Its own import folder does not exist: nothing of the first studio's folder (or the root) can be listed or picked.
assert call("get", "/api/movie-sources/config").json()["local_import"] is False
assert call("get", "/api/movie-sources/local-files").json()["detail"]["code"] == "import_root_missing"
(IMPORT / other.json()["id"]).mkdir()
assert call("get", "/api/movie-sources/local-files").json()["entries"] == []
assert add(source_type="local", path="films/movie.mp4").json()["detail"]["code"] == "not_found"
assert add(source_type="local", path=f"../{workspace}/films/movie.mp4").json()["detail"]["code"] == "invalid_path"
for method, url, body in (("get", f"/api/movie-sources/{local['id']}", None),
                          ("delete", f"/api/movie-sources/{local['id']}", None),
                          ("post", f"/api/movie-sources/{local['id']}/extend", {"days": 1}),
                          ("post", f"/api/movie-sources/{local['id']}/movie-review", {})):
    response = call(method, url, **({"json": body} if body is not None else {}))
    assert response.status_code == 404, (url, response.status_code)
assert call("post", f"/api/workspaces/{workspace}/switch").status_code == 200

# A review run uses the source: the source cannot be deleted while the run is active.
add_tool("script", "openai", "gpt-4.1-mini")
add_tool("transcription", "openai", "whisper-1")
fund(500)
started = call("post", f"/api/movie-sources/{local['id']}/movie-review",
               json={"mode": "ending_explained", "spoiler_level": "full", "tone": "critical", "duration": 120})
assert started.status_code == 201, started.text
first_run = started.json()
states = {step["node_id"]: step for step in first_run["run"]["steps"]}
assert states["movie"]["status"] == "completed" and states["prepare"]["status"] == "queued", states
current = view(local["id"])
assert current["status"] == "processing" and current["in_use"] and current["runs"][0]["id"] == first_run["run"]["id"]
refused_delete = call("delete", f"/api/movie-sources/{local['id']}")
assert refused_delete.status_code == 409 and refused_delete.json()["detail"]["code"] == "source_in_use"
with Session() as db:
    workflow = db.get(Workflow, first_run["workflow_id"])
    graph = json.loads(workflow.definition)
    script = next(item for item in graph["nodes"] if item["type"] == "review_script")
    assert script["config"]["mode"] == "ending_explained" and script["config"]["tone"] == "critical"
    assert len(graph["nodes"]) == 15
assert call("post", f"/api/movie-sources/{local['id']}/movie-review", json={"mode": "spoof"}).status_code == 422
again = call("post", f"/api/movie-sources/{local['id']}/movie-review", json={"mode": "review"}).json()
assert again["workflow_id"] == first_run["workflow_id"]  # the source's workflow is reused, not duplicated
not_ready = add(source_type="local", path="films/movie.mp4").json()
blocked = call("post", f"/api/movie-sources/{not_ready['id']}/movie-review", json={})
assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "source_not_ready"
work(2)

# The runs end without a final video: the source is ready again, kept, and can be deleted.
with Session.begin() as db:
    for run_id in (first_run["run"]["id"], again["run"]["id"]):
        run = db.get(WorkflowRun, run_id)
        run.status = "failed"
        movie_sources.run_status_changed(db, run, "running", "failed")
current = view(local["id"])
assert current["status"] == "ready" and not current["in_use"] and current["success_at"] is None
deleted = call("delete", f"/api/movie-sources/{local['id']}")
assert deleted.status_code == 200 and deleted.json()["status"] == "delete_scheduled", deleted.text
row = source(local["id"])
work()
current = view(local["id"])
assert current["status"] == "deleted" and current["deleted_at"] and not current["stored_in_drive"], current
assert drive.files[row.drive_file_id]["trashed"] is True
assert actions(local["id"])[-2:] == ["movie_source.delete_requested", "movie_source.deleted"]
assert call("delete", f"/api/movie-sources/{local['id']}").status_code == 200  # twice: harmless
assert call("post", f"/api/movie-sources/{local['id']}/movie-review", json={}).json()["detail"]["code"] == "source_expired"
with Session() as db:
    assert db.scalar(select(func.count()).select_from(MovieSourceUse).where(MovieSourceUse.movie_source_id == local["id"])) == 2

# A deletion Drive refuses is retried later, then admins are alerted; a file already gone is fine.
call("delete", f"/api/movie-sources/{url_source['id']}")
drive.fail("trash", 500)
work()
row = source(url_source["id"])
assert row.status == "delete_scheduled" and row.failure_code == "drive_server_error"
assert row.next_attempt_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
with Session.begin() as db:
    db.get(MovieSource, url_source["id"]).delete_requested_at = datetime.now(timezone.utc) - timedelta(hours=2)
with Session() as db:
    found = {condition.key for condition in alerts.conditions(db, datetime.now(timezone.utc))}
assert "movie_sources:deletion" in found, found
with Session.begin() as db:
    db.get(MovieSource, url_source["id"]).next_attempt_at = datetime.now(timezone.utc)
work()
assert view(url_source["id"])["status"] == "deleted"
assert actions(url_source["id"]).count("movie_source.drive_failed") == 1
drive._remove(source(from_drive["id"]).drive_file_id)  # someone emptied the folder by hand
call("delete", f"/api/movie-sources/{from_drive['id']}")
work()
assert view(from_drive["id"])["status"] == "deleted"
with Session() as db:
    last = db.scalars(select(AuditEvent).where(AuditEvent.target_id == from_drive["id"]).order_by(AuditEvent.id.desc())).first()
    assert json.loads(last.details_json)["drive"] == "already_gone", last.details_json

# Retention: expired sources and sources reviewed successfully (after the grace period) are scheduled for deletion;
# a source a running run uses never is.
expired = ready(source_type="local", path="films/movie.mp4")
busy = ready(source_type="local", path="films/movie.mp4")
reviewed = ready(source_type="local", path="films/movie.mp4")
busy_run = call("post", f"/api/movie-sources/{busy['id']}/movie-review", json={}).json()["run"]["id"]
done_run, final_asset = approved_run()
past = datetime.now(timezone.utc) - timedelta(days=1)
with Session.begin() as db:
    db.get(MovieSource, expired["id"]).expires_at = past
    db.get(MovieSource, busy["id"]).expires_at = past
    db.add(MovieSourceUse(id=str(uuid.uuid4()), movie_source_id=reviewed["id"], workspace_id=workspace, run_id=done_run,
                          created_at=past))
    run = db.get(WorkflowRun, done_run)
    movie_sources.run_status_changed(db, run, "awaiting_review", "completed")
reviewed_row = source(reviewed["id"])
assert reviewed_row.success_at is not None and reviewed_row.status == "ready"  # was not processing: stays ready
with Session.begin() as db:
    scheduled = movie_sources.schedule_deletions(db)
assert set(scheduled) == {expired["id"]}, scheduled  # the reviewed one waits for its 24-hour grace
with Session.begin() as db:
    db.get(MovieSource, reviewed["id"]).success_at = datetime.now(timezone.utc) - timedelta(hours=25)
    scheduled = movie_sources.schedule_deletions(db)
assert scheduled == [reviewed["id"]] and view(busy["id"])["status"] == "processing"
with Session() as db:
    reasons = {json.loads(row.details_json).get("reason") for row in db.scalars(select(AuditEvent).where(
        AuditEvent.action == "movie_source.delete_requested", AuditEvent.target_id.in_([expired["id"], reviewed["id"]])))}
assert reasons == {"expired", "after_success"}, reasons
assert movie_sources.maybe_schedule(Session, force=True) == []

# The admin console: every studio's sources with their Drive IDs, and what Drive holds.
admin_view = call("get", "/api/admin/movie-sources", params={"status": "ready"}).json()
assert admin_view["items"] and all(item["drive_file_id"] and item["workspace_name"] for item in admin_view["items"])
assert admin_view["summary"]["files"] >= 3 and admin_view["summary"]["bytes"] >= 3 * len(MOVIE)
assert call("get", "/api/admin/movie-sources", params={"status": "bogus"}).status_code == 422
# Admin → Operations: the same Drive figures beside the media storage.
operations = call("get", "/api/admin/storage").json()["movie_sources"]
assert (operations["files"], operations["bytes"]) == (admin_view["summary"]["files"], admin_view["summary"]["bytes"])
assert {"oldest_at", "expiring_soon", "delete_failures", "drive_problem"} <= set(operations), operations
readiness = call("get", "/api/admin/readiness").json()
section = next(item for item in readiness["sections"] if item["key"] == "movie_sources")
assert {check["key"]: check["status"] for check in section["checks"]}["google_drive"] == "ok", section

# No response ever carried a Drive credential, an access token or a signed URL's token.
for text in seen_text:
    for secret in DRIVE_SECRETS + ("sig=abc", "token=xyz", str(IMPORT)):
        assert secret not in text, secret
print("movie sources ok")
'''


STEPS = r'''
# The movie steps with FFmpeg and every provider faked: prepare (audio asset, frames), frame analysis in paid
# batches (success, a rate limit retried, a refusal refunded, an interrupted call held), clips from the movie.
from app.providers.text import TextProviderError
from app.workflow.nodes.movie import vision_credit_cost
configure(**{"movie_sources.enabled": True, "movie_sources.local_import_root": str(IMPORT),
             "movie_sources.drive.enabled": True, "movie_sources.drive.root_folder_id": "rootfolder01",
             "movie_sources.drive.client_id": "client-id", "movie_sources.max_frames": 25})
call("put", "/api/admin/system-config/movie_sources", json={"secrets": {
    "movie_sources.drive.client_secret": {"action": "replace", "value": "drive-client-SECRET"},
    "movie_sources.drive.refresh_token": {"action": "replace", "value": "drive-refresh-SECRET"}}})
movie = ready(source_type="local", path="films/movie.mp4")
add_tool("script", "gemini", "gemini-2.5-flash")
add_tool("transcription", "openai", "whisper-1")
fund(200)

def fake_ffmpeg(args, **kwargs):
    """ffprobe answers PROBE (clips: 4 s); ffmpeg writes the file it was asked for."""
    if Path(args[0]).name == "ffprobe":
        length = 4.0 if Path(args[-1]).name.startswith("clip-") else 125.0
        data = {**PROBE, "format": {**PROBE["format"], "duration": str(length)}}
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
    if "showinfo" in " ".join(args):
        return subprocess.CompletedProcess(args, 0, "", "[Parsed_showinfo_1] n:0 pts_time:30.5\n[Parsed_showinfo_1] n:1 pts_time:61.0")
    folder = Path(kwargs["cwd"])
    target = folder / args[-1]
    target.write_bytes(VALID_MP4 if target.suffix == ".mp4" else b"\xff\xd8\xff\xe0JPEGDATA" if target.suffix == ".jpg" else MP3)
    return subprocess.CompletedProcess(args, 0, "", "")

class Vision:
    calls = []
    plan = []
    def __init__(self, name):
        self.name = name
    def generate(self, **kwargs):
        Vision.calls.append(kwargs)
        action = Vision.plan.pop(0) if Vision.plan else "ok"
        if action == "rate":
            raise TextProviderError("rate_limited", "slow down", retryable=True)
        if action == "refuse":
            raise TextProviderError("invalid_request", "bad image")
        if action == "lost":
            raise TextProviderError("submission_unknown", "connection lost", retryable=True)
        frames = [int(part.split()[1]) for part in kwargs["prompt"].splitlines() if part.startswith("Frame ")]
        notes = [{"index": index, "description": f"Frame {index}: a person walks", "importance": 0.5} for index in frames]
        return TextResult(text=json.dumps({"frames": notes}), usage=TextUsage.of(100, 50), provider=self.name,
                          model=kwargs["model"])
    def close(self):
        pass

def run_jobs(limit=40):
    count = 0
    while movie_worker.run_job(session_factory=Session, provider_factory=Vision, runner=fake_ffmpeg, worker_id="w"):
        count += 1
        assert count < limit
    return count

started = call("post", f"/api/movie-sources/{movie['id']}/movie-review", json={"duration": 60}).json()
run_id = started["run"]["id"]
cost = vision_credit_cost()
Vision.plan = ["ok", "rate", "refuse"]  # batch 1 described; batch 2 rate-limited once, then refused
run_jobs()
run, states = steps(run_id)
prepare = states["prepare"]
assert prepare["status"] == "completed", prepare
output = prepare["output"]
assert 10 < output["frame_count"] == len(output["frames"]) <= 25 and output["scene_cut_count"] == 2, output
assert any(frame["cut"] for frame in output["frames"])
assert output["movie"]["id"] == movie["id"] and output["movie"]["scene_cuts"] == [30.5, 61.0]
assert prepare["detail"].startswith("Đã chuẩn bị phim") and output["progress"]["stage"] == "done"
audio = output["audio_assets"][0]
with Session() as db:
    asset = db.get(Asset, audio["id"])
    assert asset.kind == "movie_audio" and asset.content_type == "audio/mpeg" and asset.movie_source_id == movie["id"]
scratch = movie_sources.work_dir(movie_sources.scratch_root(Session()), movie["id"])
assert (scratch / "source.mp4").read_bytes() == MOVIE and (scratch / "source.ok").read_text() == hashlib.md5(MOVIE).hexdigest()
frames_dir = movie_sources.run_dir(movie_sources.scratch_root(Session()), movie["id"], run_id) / "frames"
assert len(list(frames_dir.glob("f-*.jpg"))) == output["frame_count"]
assert states["transcript"]["status"] == "queued", states["transcript"]
assert states["visual"]["status"] == "running" and states["visual"]["output"]["expected"] == 2, states["visual"]
assert states["visual"]["output"]["progress"] == {"stage": "vision", "done": 1, "total": 2, "frames_done": 10,
                                                  "frames_total": output["frame_count"]}
with Session.begin() as db:  # the rate-limited batch waits for its retry delay: make it due now
    db.execute(update(WorkflowJob).where(WorkflowJob.logical_key.like("movie:%"), WorkflowJob.state == "queued")
               .values(available_at=datetime.now(timezone.utc)))
run_jobs()
run, states = steps(run_id)
visual = states["visual"]
assert visual["status"] == "completed", visual  # one batch described, the refused one refunded
assert visual["output"]["visual"]["batches_succeeded"] == 1 and visual["output"]["visual"]["frame_count"] == 10
images = Vision.calls[0]["images"]
assert len(images) == 10 and images[0].mime_type == "image/jpeg" and "Never identify" in Vision.calls[0]["system_prompt"]
assert len(Vision.calls) == 3  # ok, rate-limited, refused: the refused batch is never sent again
with Session() as db:
    ledger = {reason: sum(row.delta for row in db.scalars(select(CreditLedger).where(CreditLedger.reason == reason)))
              for reason in ("vision_reserve", "vision_refund")}
    assert ledger == {"vision_reserve": -2 * cost, "vision_refund": cost}, ledger
    usage_rows = db.scalars(select(UsageEvent).where(UsageEvent.reference.like("vision:%"))).all()
    assert len(usage_rows) == 1 and usage_rows[0].tool == "gemini/vision" and usage_rows[0].credits == cost
assert states["timeline"]["status"] == "skipped"  # still waits for the transcript

# A worker that died after calling the provider: the batch is held for reconciliation, never sent again.
second = call("post", f"/api/movie-sources/{movie['id']}/movie-review", json={}).json()["run"]["id"]
downloads = sum(1 for method, path in drive.calls if method == "GET" and path.startswith("/drive/v3/files/"))
assert movie_worker.run_job(session_factory=Session, provider_factory=Vision, runner=fake_ffmpeg, worker_id="w")
assert steps(second)[1]["prepare"]["status"] == "completed"
assert sum(1 for method, path in drive.calls if method == "GET" and path.startswith("/drive/v3/files/")) == downloads
with Session.begin() as db:
    job = db.scalars(select(WorkflowJob).where(WorkflowJob.run_id == second,
                                               WorkflowJob.logical_key.like("%:batch:1"))).one()
    claimed = jobs.claim_due_jobs(db, worker_id="crashed", limit=1, lease_seconds=60, logical_key_prefix=job.logical_key)
    step = db.get(WorkflowRunStep, job.step_id)
    assert movie_worker._vision_begin(db, claimed[0], step) == "call"
    output = json.loads(step.output)
    output["jobs"][job.id]["provider_job"] = {"submission_started_at": datetime.now(timezone.utc).isoformat()}
    step.output = json.dumps(output)
    db.execute(update(WorkflowJob).where(WorkflowJob.id == job.id).values(
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    crashed_job = job.id
calls_before = len(Vision.calls)
run_jobs()
run, states = steps(second)
assert states["visual"]["status"] == "needs_attention", states["visual"]
assert len(Vision.calls) == calls_before + 1  # only the other batch was sent
pending = call("get", "/api/admin/reconciliation").json()["items"]
item = next(entry for entry in pending if entry["job_id"] == crashed_job)
assert item["node_type"] == "visual_analysis" and item["operation"] == "batch:1" and item["credits"] == cost
held = balance()
refunded = call("post", f"/api/admin/reconciliation/jobs/{crashed_job}/refund", json={"note": "provider shows no charge"})
assert refunded.status_code == 200, refunded.text
assert balance() == held + cost
assert call("post", f"/api/admin/reconciliation/jobs/{crashed_job}/refund", json={}).status_code in (200, 409)
assert balance() == held + cost  # never twice

# Clips: the Clip Selector's excerpts are cut from the scratch copy by the movie worker.
from app.clip_selection import select as select_clips
scenes = [{"index": 1, "text": "word " * 20, "source_ranges": [{"start": 10, "end": 18}]},
          {"index": 2, "text": "word " * 12, "source_ranges": [{"start": 60, "end": 66}]}]
chosen = select_clips(scenes, duration=125.0, movie_source_id=movie["id"], content_type="video/mp4")
assert chosen["clips"] and all(clip["movie_source_id"] == movie["id"] for clip in chosen["clips"])
with Session.begin() as db:
    step = WorkflowRunStep(id=str(uuid.uuid4()), run_id=second, node_id="extra-clips", node_type="extract_clips",
                           position=99, status="queued", detail="", output=json.dumps({"expected": len(chosen["clips"])}))
    db.add(step)
    db.flush()
    jobs.enqueue_job(db, run_id=second, step_id=step.id, workspace_id=workspace, logical_key=f"movie:{step.id}:clips",
                     payload={"kind": "movie.clip_extract", "node_type": "extract_clips", "node_id": "extra-clips",
                              "provider": "ffmpeg", "model": "local", "mode": "copy_first",
                              "movie_source_id": movie["id"], "content_type": "video/mp4",
                              "clips": [{key: clip[key] for key in ("movie_source_id", "start", "end", "scene_index")}
                                        for clip in chosen["clips"]]})
run_jobs()
with Session() as db:
    step = db.scalars(select(WorkflowRunStep).where(WorkflowRunStep.node_id == "extra-clips")).one()
    output = json.loads(step.output)
    assert step.status == "completed", (step.status, step.detail)
    assert output["clip_count"] == len(chosen["clips"]) and output["movie_source_id"] == movie["id"]
    clips = db.scalars(select(Asset).where(Asset.step_id == step.id)).all()
    assert clips and all(asset.kind == "extracted_clip" and asset.movie_source_id == movie["id"] for asset in clips)
    assert all(entry["cut"] == "copy" and entry["source_start"] >= 0 for entry in output["video_assets"])

# Admin → Operations lists the movie step jobs under their own queue.
movie_jobs = call("get", "/api/admin/jobs", params={"queue": "movie", "limit": 200}).json()
assert movie_jobs["total"] >= 3 and all(job["queue"] == "movie" for job in movie_jobs["jobs"]), movie_jobs["total"]
assert movie_jobs["counts"]["movie"].get("succeeded"), movie_jobs["counts"]

# Scratch space: a run's folder goes once the run has ended, the movie copy once no run uses the source.
with Session.begin() as db:
    for rid in (run_id, second):
        db.get(WorkflowRun, rid).status = "failed"
    movie_sources.refresh_processing(db)
assert movie_worker.sweep_scratch(Session, force=True) >= 2
assert not frames_dir.exists() and (scratch / "source.mp4").exists()  # recently used: kept for a while
with unittest_patch(movie_worker, "SCRATCH_IDLE_SECONDS", -1):
    movie_worker.sweep_scratch(Session, force=True)
assert not scratch.exists()
print("movie steps ok")
'''


REAL = r'''
# A real 40-second movie, real FFmpeg and ffprobe, every AI provider faked: from the import to the final MP4,
# the review, and the source's deletion once the review succeeded.
FFMPEG, FFPROBE = os.environ["MOVIE_TEST_FFMPEG"], os.environ["MOVIE_TEST_FFPROBE"]
os.environ.update({"RENDER_FFMPEG_PATH": FFMPEG, "RENDER_FFPROBE_PATH": FFPROBE})
from app import source_worker, voice_worker
from app.audio_files import pcm_to_wav
from app.providers.voice import VoiceResult

synthetic = STUDIO_IMPORT / "films" / "synthetic.mp4"
subprocess.run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=14",
                "-f", "lavfi", "-i", "smptebars=size=640x360:rate=25:duration=13",
                "-f", "lavfi", "-i", "color=c=0x1050c0:size=640x360:rate=25:duration=13",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=40",
                "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "50", "-c:a", "aac",
                "-shortest", str(synthetic)], check=True)
configure(**{"movie_sources.enabled": True, "movie_sources.local_import_root": str(IMPORT),
             "movie_sources.drive.enabled": True, "movie_sources.drive.root_folder_id": "rootfolder01",
             "movie_sources.drive.client_id": "client-id", "movie_sources.frame_interval_seconds": 5})
call("put", "/api/admin/system-config/movie_sources", json={"secrets": {
    "movie_sources.drive.client_secret": {"action": "replace", "value": "drive-client-SECRET"},
    "movie_sources.drive.refresh_token": {"action": "replace", "value": "drive-refresh-SECRET"}}})
created = add(source_type="local", path="films/synthetic.mp4")
assert created.status_code == 201, created.text
for _ in range(2):
    assert movie_worker.run_source_work(session_factory=Session, runner=subprocess.run, worker_id="real")
movie = view(created.json()["id"])
assert movie["status"] == "ready" and 39 <= movie["duration_seconds"] <= 41, movie
assert (movie["width"], movie["height"], movie["container"], movie["audio_codec"]) == (640, 360, "mp4", "aac"), movie

add_tool("script", "gemini", "gemini-2.5-flash")
add_tool("transcription", "openai", "whisper-1")
add_tool("voice", "gemini", "gemini-2.5-flash-preview-tts")
fund(1000)
STORY = json.dumps({"title": "Ba cảnh", "summary": "Một phim thử nghiệm ba cảnh.", "setup": "Màn hình thử",
                    "climax": "Màu xanh", "ending": "Kết thúc", "plot_points": ["Mở đầu", "Giữa", "Kết"],
                    "turning_points": [{"description": "Chuyển cảnh", "start": 14}], "acts": [],
                    "important_moments": [], "themes": ["thử nghiệm"]}, ensure_ascii=False)
REVIEW = json.dumps({"title": "Review: Ba cảnh", "sections": [
    {"text": "Phim mở đầu bằng một màn hình thử đầy màu sắc.", "kind": "fact",
     "source_ranges": [{"start": 2, "end": 8}], "importance": 0.6},
    {"text": "Rồi những vạch màu xuất hiện và nhịp phim chậm lại.", "kind": "fact",
     "source_ranges": [{"start": 16, "end": 22}], "importance": 0.5},
    {"text": "Cái kết màu xanh đơn giản nhưng hiệu quả.", "kind": "opinion",
     "source_ranges": [{"start": 30, "end": 36}], "importance": 0.8}]}, ensure_ascii=False)
META = json.dumps({"title": "Review ba cảnh", "description": "Một review ngắn.", "tags": ["review"]}, ensure_ascii=False)
replies = {"analyse the story": STORY, "recap and review scripts": REVIEW, "publishing metadata": META}


class RealVision:
    calls = 0

    def __init__(self, name):
        self.name = name

    def generate(self, **kwargs):
        RealVision.calls += 1
        assert all(image.data[:2] == b"\xff\xd8" for image in kwargs["images"])  # real JPEG frames
        frames = [int(part.split()[1]) for part in kwargs["prompt"].splitlines() if part.startswith("Frame ")]
        notes = [{"index": index, "description": f"Khung hình {index}: màu sắc thay đổi", "importance": 0.4}
                 for index in frames]
        return TextResult(text=json.dumps({"frames": notes}), usage=TextUsage.of(80, 40), provider=self.name,
                          model=kwargs["model"])

    def close(self):
        pass


class Voices:
    def generate(self, request):
        return VoiceResult("gemini", request.model, pcm_to_wav(b"\x00\x00" * 24000 * 3, sample_rate=24000),
                           "audio/wav", 3.0, "resp")

    def close(self):
        pass


transcriber = Transcriber(segments=[(1.0, 5.0, "Xin chào, đây là phim thử"), (15.0, 19.0, "Những vạch màu"),
                                    (31.0, 35.0, "Kết thúc màu xanh")])
started = call("post", f"/api/movie-sources/{movie['id']}/movie-review",
               json={"duration": 30, "section_count": 3, "language": "vi", "tone": "cinematic"})
assert started.status_code == 201, started.text
run_id = started.json()["run"]["id"]
for _ in range(80):
    progressed = [movie_worker.run_job(session_factory=Session, provider_factory=RealVision, runner=subprocess.run,
                                       worker_id="real"),
                  source_worker.run_one(provider_factory=lambda name: transcriber, session_factory=Session),
                  text_worker.run_one(provider_factory=writer(replies)),
                  voice_worker.run_one(client=Voices()),
                  render_worker.run_one()]
    state = client.get(f"/api/workflow-runs/{run_id}").json()
    if state["status"] != "running" or not any(progressed):
        break
states = {step["node_id"]: step for step in state["steps"]}
assert state["status"] == "awaiting_review", {key: (step["status"], step["detail"]) for key, step in states.items()}
prepare = states["prepare"]["output"]
assert 8 <= prepare["frame_count"] <= 40 and prepare["audio_assets"], prepare["frame_count"]
assert RealVision.calls == -(-prepare["frame_count"] // 10)
timeline = states["timeline"]["output"]
assert timeline["window_count"] >= 3 and timeline["timeline"]["windows"][0]["dialogue"].startswith("Xin chào"), timeline
story_prompt = next(item["prompt"] for item in WRITER_PROMPTS if "analyse the story" in (item.get("system_prompt") or "").lower()
                    or "Analyse the story" in item["prompt"])
assert "Dialogue: Xin chào" in story_prompt and "movie timeline" in story_prompt
assert states["analysis"]["output"]["analysis"]["setup"] == "Màn hình thử"
scenes = states["script"]["output"]["scenes"]
assert [scene["source_ranges"][0]["start"] for scene in scenes] == [2.0, 16.0, 30.0], scenes
review_prompt = next(item["prompt"] for item in WRITER_PROMPTS if "source_ranges" in item["prompt"])
assert "a review:" in review_prompt and "Tone: cinematic" in review_prompt, review_prompt[:500]
selected = states["select"]["output"]["source_clips"]
assert [clip["scene_index"] for clip in selected] == [1, 2, 3], selected
assert all(2.0 <= clip["end"] - clip["start"] <= 8.0 for clip in selected)
assert all(start - 0.5 <= clip["start"] <= start + 6 for clip, start in zip(selected, (2, 16, 30))), selected
clips = states["clips"]["output"]["video_assets"]
assert len(clips) == 3 and all(entry["movie_source_id"] == movie["id"] for entry in clips)


def probe_file(asset_id):
    with Session() as db:
        path = media_root(db) / workspace / asset_id
    info = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                                      "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)
    return float(info["format"]["duration"]), {stream["codec_type"] for stream in info["streams"]}


for entry in clips:
    seconds, kinds = probe_file(entry["id"])
    assert 1.5 <= seconds <= 12 and "video" in kinds, (seconds, kinds)
final = states["render"]["output"]["video_assets"][0]
seconds, kinds = probe_file(final["id"])
assert 6 <= seconds <= 30 and kinds == {"video", "audio"}, (seconds, kinds)

# The review is approved: the run succeeds, the source is completed and, after the grace period, deleted.
approved = call("post", f"/api/workflow-runs/{run_id}/approve")
assert approved.status_code == 200 and approved.json()["status"] == "completed", approved.text
current = view(movie["id"])
assert current["status"] == "completed" and current["success_at"] and not current["in_use"], current
with Session.begin() as db:
    assert movie_sources.schedule_deletions(db) == []  # within the 24-hour grace
    db.get(MovieSource, movie["id"]).success_at = datetime.now(timezone.utc) - timedelta(hours=25)
    assert movie_sources.schedule_deletions(db) == [movie["id"]]
row = source(movie["id"])
assert movie_worker.run_source_work(session_factory=Session, runner=subprocess.run, worker_id="real")
assert view(movie["id"])["status"] == "deleted" and drive.files[row.drive_file_id]["trashed"]
movie_worker.sweep_scratch(Session, force=True)
assert not movie_sources.work_dir(movie_sources.scratch_root(Session()), movie["id"]).exists()
# The final video and the transcript stay: only the temporary source went.
assert probe_file(final["id"])[0] == seconds
after = {step["node_id"]: step for step in client.get(f"/api/workflow-runs/{run_id}").json()["steps"]}
assert after["transcript"]["status"] == "completed" and after["transcript"]["output"]["transcript"]["text"]
print("real movie pipeline ok")
'''


HELPERS = r'''
from unittest.mock import patch
unittest_patch = patch.object
'''


def real_tools() -> tuple[str, str] | None:
    """FFmpeg and ffprobe for the real-movie test: REELFORGE_TEST_FFMPEG / _FFPROBE, else the ones on PATH."""
    ffmpeg = os.environ.get("REELFORGE_TEST_FFMPEG") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("REELFORGE_TEST_FFPROBE") or shutil.which("ffprobe")
    return (ffmpeg, ffprobe) if ffmpeg and ffprobe else None


class MovieSourcesTest(unittest.TestCase):
    def run_body(self, body: str, marker: str, env: dict | None = None):
        result = run_program(HELPERS + fake_drive_source() + COMMON + body, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(marker, result.stdout)

    def test_sources_lifecycle(self):
        self.run_body(SOURCES, "movie sources ok")

    def test_movie_steps(self):
        self.run_body(STEPS, "movie steps ok")

    def test_real_movie_pipeline(self):
        tools = real_tools()
        if tools is None:
            if os.environ.get("REELFORGE_REQUIRE_FFMPEG") == "1":  # CI installs FFmpeg: never skip there
                self.fail("REELFORGE_REQUIRE_FFMPEG=1 but FFmpeg and ffprobe were not found")
            self.skipTest("FFmpeg and ffprobe are not installed")
        self.run_body(REAL, "real movie pipeline ok",
                      env={"MOVIE_TEST_FFMPEG": tools[0], "MOVIE_TEST_FFPROBE": tools[1]})


if __name__ == "__main__":
    unittest.main()
