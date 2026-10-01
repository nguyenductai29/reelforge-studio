"""Phase 10 end to end: document uploads, source steps, URL fetching and paid transcription. Offline."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_harness import run_program  # noqa: E402

UPLOADS = r'''
SRT = "1\n00:00:01,000 --> 00:00:03,000\nXin chào\n\n2\n00:00:05,000 --> 00:00:07,500\nTạm biệt\n"
# Text documents: the extension decides when the browser sends no type or a generic one.
md = upload("ghi-chu.md", "# Rừng\n\nNội dung ghi chú.".encode(), "application/octet-stream")
assert md.status_code == 201 and md.json()["content_type"] == "text/markdown", md.text
srt = upload("phim.srt", SRT.encode(), "")
assert srt.status_code == 201 and srt.json()["content_type"] == "application/x-subrip", srt.text
vtt = upload("phim.vtt", b"WEBVTT\n\n00:01.000 --> 00:02.000\nHi\n", "text/vtt")
assert vtt.status_code == 201, vtt.text
assert upload("x.txt", b"\x00\x01\x02 binary", "text/plain").status_code == 415
assert upload("x.vtt", b"no header", "text/vtt").status_code == 415
assert upload("x.srt", "không có mốc thời gian".encode(), "application/x-subrip").status_code == 415
assert upload("x.exe", b"MZ", "application/octet-stream").status_code == 415
image = upload("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png").json()["id"]

# A file setting must name this workspace's own upload, of a type the step reads (Music reads audio only).
workflow, saved = save_graph([node("bgm", "music", {"asset_id": image})], [])
assert saved.status_code == 422 and saved.json()["detail"]["code"] == "invalid_asset", saved.text
created = client.post("/api/admin/accounts", json={"email": "other@example.com", "password": "long-password-123",
                                                   "workspace_name": "Other", "plan_code": "trial"})
assert created.status_code == 201, created.text
other = TestClient(app, headers={"Origin": "http://testserver"})
assert other.post("/api/login", json={"email": "other@example.com", "password": "long-password-123"}).status_code == 200
foreign = other.post("/api/assets", files={"file": ("x.txt", "Nội dung khác".encode(), "text/plain")}).json()["id"]
_, saved = save_graph([node("src", "source_media", {"asset_id": foreign})], [])
assert saved.status_code == 422 and saved.json()["detail"]["code"] == "invalid_asset", saved.text
_, saved = save_graph([node("src", "source_media", {"asset_id": md.json()["id"]})], [])
assert saved.status_code == 200, saved.text

# Text Source → Summarize: the pasted text reaches the text model.
add_tool("script", "openai", "gpt-4.1-mini")
fund(20)
workflow, saved = save_graph([node("src", "source_text", {"title": "Ghi chú", "text": "Con cú bay qua khu rừng lúc nửa đêm."}),
                              node("sum", "summarize", x=300)], [edge("src", "text", "sum", "text")])
assert saved.status_code == 200, saved.text
readiness = client.get(f"/api/workflows/{workflow}/readiness").json()
assert {s["node_id"]: s["status"] for s in readiness["steps"]} == {"src": "configured", "sum": "ready"}, readiness
run = start(workflow)
drain(text_worker, provider_factory=writer())
state, by_node = steps(run["id"])
assert state["status"] == "completed", state
assert by_node["src"]["output"]["source"]["source_type"] == "text"
assert "Con cú bay qua khu rừng" in WRITER_PROMPTS[-1]["prompt"]

# An empty Text Source blocks its run with a clear reason instead of calling the model.
workflow, _ = save_graph([node("src", "source_text"), node("sum", "summarize", x=300)], [edge("src", "text", "sum", "text")])
assert next(s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"] if s["node_id"] == "src")["status"] == "missing_input"
state, by_node = steps(start(workflow)["id"])
assert by_node["src"]["status"] == "blocked", by_node

# Subtitles already carry timed text: Transcript passes them through for free, without a job.
workflow, saved = save_graph([node("src", "source_media", {"asset_id": srt.json()["id"]}),
                              node("tr", "transcribe", x=300)], [edge("src", "source", "tr", "source")])
assert saved.status_code == 200, saved.text
checks = {s["node_id"]: s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert (checks["tr"]["status"], checks["tr"]["credits"]) == ("configured", 0), checks
before = balance()
state, by_node = steps(start(workflow)["id"])
assert state["status"] == "completed", state
transcript = by_node["tr"]["output"]["transcript"]
assert [s["start"] for s in transcript["segments"]] == [1.0, 5.0], transcript
assert balance() == before
with Session() as db:
    assert db.scalar(select(func.count()).select_from(WorkflowJob).where(WorkflowJob.logical_key.like("source:%"))) == 0
print("uploads ok")
'''

URL_SOURCE = r'''
import socket
add_tool("script", "openai", "gpt-4.1-mini")
fund(20)
PAGE = ("<html lang='en'><head><title>Night forest</title></head><body><nav>Home</nav><article><p>"
        + "Owls hunt quietly between the old trees at night. " * 6 + "</p></article></body></html>")
seen = []
def handler(request):
    seen.append(request)
    return httpx.Response(200, headers={"Content-Type": "text/html"}, content=PAGE.encode())
public = lambda host, port, type=None: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

workflow, saved = save_graph([node("url", "source_url", {"url": "https://example.com/owls?token=abc"}),
                              node("sum", "summarize", x=300)], [edge("url", "text", "sum", "text")])
assert saved.status_code == 200, saved.text
run = start(workflow)
state, by_node = steps(run["id"])
assert by_node["url"]["status"] == "queued", by_node
with httpx.Client(transport=httpx.MockTransport(handler)) as mock:
    assert source_worker.run_one(client=mock, resolver=public)
assert (seen[0].url.host, seen[0].headers["Host"]) == ("93.184.216.34", "example.com")
drain(text_worker, provider_factory=writer())
state, by_node = steps(run["id"])
assert state["status"] == "completed", state
assert by_node["url"]["output"]["title"] == "Night forest"
assert "Owls hunt quietly" in WRITER_PROMPTS[-1]["prompt"]
# Operators see jobs without their payload (the URL may carry a token).
jobs_view = client.get("/api/admin/jobs?queue=source").text
assert "token=abc" not in jobs_view and '"payload' not in jobs_view, jobs_view

# Private addresses are refused before anything is queued, and again by the worker after DNS.
workflow, _ = save_graph([node("url", "source_url", {"url": "https://127.0.0.1/admin"}), node("sum", "summarize", x=300)],
                         [edge("url", "text", "sum", "text")])
check = next(s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"] if s["node_id"] == "url")
assert (check["status"], check["code"]) == ("invalid_settings", "blocked_url"), check
state, by_node = steps(start(workflow)["id"])
assert by_node["url"]["status"] == "blocked", by_node
workflow, _ = save_graph([node("url", "source_url", {"url": "https://rebind.example/x"}), node("sum", "summarize", x=300)],
                         [edge("url", "text", "sum", "text")])
run = start(workflow)
private = lambda host, port, type=None: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", port))]
calls = []
with httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))) as mock:
    assert source_worker.run_one(client=mock, resolver=private)
state, by_node = steps(run["id"])
assert (by_node["url"]["status"], by_node["url"]["output"]["error"]["code"], calls) == ("failed", "blocked_url", []), by_node
# A timeout is tried again; after the last attempt the step fails with that code.
workflow, _ = save_graph([node("url", "source_url", {"url": "https://slow.example/x"}), node("sum", "summarize", x=300)],
                         [edge("url", "text", "sum", "text")])
run = start(workflow)
def slow(request):
    raise httpx.ReadTimeout("slow", request=request)
with Session.begin() as db:
    db.execute(update(WorkflowJob).where(WorkflowJob.logical_key.like("source:%"), WorkflowJob.state == "queued")
               .values(available_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
with httpx.Client(transport=httpx.MockTransport(slow)) as mock:
    for _ in range(4):
        with Session.begin() as db:
            db.execute(update(WorkflowJob).where(WorkflowJob.logical_key.like("source:%"), WorkflowJob.state == "queued")
                       .values(available_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        source_worker.run_one(client=mock, resolver=public)
state, by_node = steps(run["id"])
assert (by_node["url"]["status"], by_node["url"]["output"]["error"]["code"]) == ("failed", "timeout"), by_node
print("url ok")
'''

TRANSCRIPTION = r'''
audio = upload("podcast.mp3", MP3, "audio/mpeg").json()["id"]
tool = add_tool("transcription", "openai", "whisper-1")
fund(10)
workflow, saved = save_graph([node("src", "source_media", {"asset_id": audio}), node("tr", "transcribe", x=300)],
                             [edge("src", "source", "tr", "source")])
assert saved.status_code == 200, saved.text
checks = {s["node_id"]: s for s in client.get(f"/api/workflows/{workflow}/readiness").json()["steps"]}
assert (checks["tr"]["status"], checks["tr"]["credits"], checks["tr"]["tool"]["id"]) == ("ready", 2, tool), checks

# Two 20-minute pieces: the second piece's timestamps are shifted by 1200 s.
run = start(workflow)
state, by_node = steps(run["id"])
assert by_node["tr"]["status"] == "queued" and balance() == 8, (by_node, balance())
fake = Transcriber([(1.0, 3.5, "Xin chào"), (4.0, 6.0, "các bạn")])
runner = ffmpeg_runner(duration=1500.0, parts=2)
assert source_worker.run_one(runner=runner, provider_factory=lambda name: fake)
state, by_node = steps(run["id"])
output = by_node["tr"]["output"]
assert state["status"] == "completed", state
assert [(s["start"], s["text"]) for s in output["transcript"]["segments"]] == \
       [(1.0, "Xin chào"), (4.0, "các bạn"), (1201.0, "Xin chào"), (1204.0, "các bạn")], output
assert (output["transcript"]["asset_id"], output["transcript"]["metadata"]["duration"], output["segment_count"]) == (audio, 1500.0, 4)
assert [call[0] for call in fake.calls] == ["part-000.mp3", "part-001.mp3"]
extract = next(c for c in runner.calls if Path(c[0]).name == "ffmpeg")
assert "-vn" in extract and "segment" in extract
assert balance() == 8
with Session() as db:
    step_id = db.scalar(select(WorkflowRunStep.id).where(WorkflowRunStep.run_id == run["id"], WorkflowRunStep.node_id == "tr"))
    assert db.scalar(select(UsageEvent.credits).where(UsageEvent.reference == f"transcription:{step_id}:single")) == 2
    assert not list(media_root(db).joinpath(".source-tmp").glob("*"))  # the temp folder is always removed

def transcribe_run(error=None, runner=None):
    fund(2)
    run = start(workflow)
    source_worker.run_one(runner=runner or ffmpeg_runner(), provider_factory=lambda name: Transcriber(error=error))
    return run["id"], steps(run["id"])[1]["tr"]

# A definite rejection refunds; a lost answer holds the credits for reconciliation.
start_balance = balance()
_, step = transcribe_run(TranscriptionProviderError("authentication_error", "bad key"))
assert (step["status"], step["output"]["error"]["code"]) == ("failed", "authentication_error"), step
assert balance() == start_balance + 2  # funded 2, reserved 2, refunded 2
start_balance = balance()
run_id, step = transcribe_run(TranscriptionProviderError("submission_unknown", "lost", category="network_error"))
assert step["status"] == "needs_attention", step
assert balance() == start_balance  # funded 2, the reservation is still held
pending = client.get("/api/admin/reconciliation").json()["items"]
item = next(i for i in pending if i["run_id"] == run_id)
assert (item["node_type"], item["credits"]) == ("transcribe", 2), item
refunded = client.post(f"/api/admin/reconciliation/jobs/{item['job_id']}/refund", json={"note": "provider shows nothing"})
assert refunded.status_code == 200, refunded.text
assert balance() == start_balance + 2

# Local problems (no audio track) refund before the provider is ever called.
start_balance = balance()
_, step = transcribe_run(runner=ffmpeg_runner(has_audio=False))
assert (step["status"], step["output"]["error"]["code"]) == ("failed", "no_audio"), step
assert balance() == start_balance + 2

# A worker that crashed after the provider call started never sends the audio again.
fund(2)
run = start(workflow)
with Session.begin() as db:
    step = db.scalar(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run["id"], WorkflowRunStep.node_id == "tr"))
    step.status = "running"
    step.output = json.dumps({"provider_job": {"stage": "submitting",
                                               "submission_started_at": datetime.now(timezone.utc).isoformat()}})
never = Transcriber(error=AssertionError("must not be called"))
assert source_worker.run_one(runner=ffmpeg_runner(), provider_factory=lambda name: never)
assert steps(run["id"])[1]["tr"]["status"] == "needs_attention" and never.calls == []
print("transcription ok")
'''

TEMPLATES = r'''
listed = {t["id"]: t for t in client.get("/api/workflow-templates").json()["templates"]}
for template_id in ("repurpose", "movie_recap", "tiktok_short", "facebook_reel"):
    created = client.post("/api/workflows", json={"name": template_id, "template": template_id})
    assert created.status_code == 201, created.text
    types = [n["type"] for n in created.json()["graph"]["nodes"]]
    assert all(edge["sourceHandle"] and edge["targetHandle"] for edge in created.json()["graph"]["edges"])
assert listed["movie_recap"]["notice"] == "Use only content you are authorized to use."
catalog = client.get("/api/workflow-node-types").json()
assert {"source", "source_clips", "story"} <= set(catalog["data_types"])
asset_field = next(f for f in catalog["node_types"]["source_media"]["config"] if f["key"] == "asset_id")
assert asset_field["type"] == "asset" and "application/x-subrip" in asset_field["content_types"]
print("templates ok")
'''


class SourceWorkflowTest(unittest.TestCase):
    def check(self, body):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])

    def test_document_uploads_sources_and_free_transcript_pass_through(self):
        self.check(UPLOADS)

    def test_url_source_is_fetched_safely_by_the_source_worker(self):
        self.check(URL_SOURCE)

    def test_transcription_is_a_durable_paid_job(self):
        self.check(TRANSCRIPTION)

    def test_new_templates_and_node_catalog(self):
        self.check(TEMPLATES)


if __name__ == "__main__":
    unittest.main()
