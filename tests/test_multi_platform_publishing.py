"""Phase 12 end to end: TikTok and Facebook OAuth, channel status, multi-platform publishing and uploads.

Every platform call is answered by ``httpx.MockTransport``; nothing is posted anywhere.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_harness import run_program  # noqa: E402

OAUTH = r'''
from urllib.parse import parse_qs, urlsplit
from app import social_worker

statuses = {c["channel"]: c["status"] for c in client.get("/api/channels").json()["channels"]}
assert statuses == {"youtube": "not_connected", "tiktok": "not_connected", "facebook": "not_connected"}, statuses
secret = os.environ.pop("FACEBOOK_APP_SECRET")
assert next(c for c in client.get("/api/channels").json()["channels"] if c["channel"] == "facebook")["status"] == "configuration_required"
assert client.get("/api/channels/facebook/authorization").status_code == 503
os.environ["FACEBOOK_APP_SECRET"] = secret

calls = []
def platform(request):
    calls.append(request)
    url = str(request.url)
    body = request.content.decode()
    if url == channel_oauth.TIKTOK_TOKEN:
        assert "tiktok-SECRET" in body  # the secret goes in the POST body, never the URL
        if "grant_type=refresh_token" in body:
            return httpx.Response(200, json={"access_token": "tok-tt-access-2", "expires_in": 86400, "open_id": "open-1",
                                             "refresh_token": "tok-tt-refresh-2", "refresh_expires_in": 31536000,
                                             "scope": "user.info.basic,video.upload", "token_type": "Bearer"})
        return httpx.Response(200, json={"access_token": "tok-tt-access", "expires_in": 86400, "open_id": "open-1",
                                         "refresh_token": "tok-tt-refresh", "refresh_expires_in": 31536000,
                                         "scope": "user.info.basic,video.upload", "token_type": "Bearer"})
    if url == channel_oauth.TIKTOK_USER_INFO:
        return httpx.Response(200, json={"data": {"user": {"open_id": "open-1", "display_name": "Creator"}},
                                         "error": {"code": "ok"}})
    if url == channel_oauth.FACEBOOK_TOKEN:
        if "fb_exchange_token" in body:
            return httpx.Response(200, json={"access_token": "tok-fb-long", "token_type": "bearer", "expires_in": 5184000})
        return httpx.Response(200, json={"access_token": "tok-fb-short", "token_type": "bearer", "expires_in": 3600})
    if url == channel_oauth.FACEBOOK_PAGES:
        return httpx.Response(200, json={"data": [
            {"id": "111", "name": "Page A", "access_token": "tok-fb-page-a", "tasks": ["CREATE_CONTENT", "MANAGE"]},
            {"id": "222", "name": "Page B", "access_token": "tok-fb-page-b", "tasks": ["CREATE_CONTENT"]},
            {"id": "333", "name": "Analyst", "access_token": "tok-fb-analyst", "tasks": ["ANALYZE"]}]})
    raise AssertionError(url)
main.google_http_client = lambda: httpx.Client(transport=httpx.MockTransport(platform))

def authorize(channel):
    response = client.get(f"/api/channels/{channel}/authorization")
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["url"]).query)
    return response.json()["url"], query

url, query = authorize("tiktok")
assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?"), url
assert (query["client_key"], query["scope"], query["redirect_uri"], query["response_type"]) == \
       (["tiktok-client"], ["user.info.basic,video.upload"], ["http://localhost:3000/channels/callback/tiktok"], ["code"])
state = query["state"][0]
with Session() as db:
    assert db.get(channel_oauth.ChannelOAuthState, state) is None  # only its hash is stored
done = client.post("/api/channels/tiktok/callback", json={"state": state, "code": "code-1"})
assert done.status_code == 200, done.text
assert (done.json()["status"], done.json()["account_name"]) == ("connected", "Creator"), done.json()
no_secrets(done.text)
replay = client.post("/api/channels/tiktok/callback", json={"state": state, "code": "code-1"})
assert replay.status_code == 400, replay.text
wrong = client.post("/api/channels/facebook/callback", json={"state": authorize("tiktok")[1]["state"][0], "code": "c"})
assert wrong.status_code == 400  # a TikTok state cannot complete a Facebook connection
with Session() as db:
    row = db.get(channel_oauth.ChannelConnection, (workspace, "tiktok"))
    assert "tok-tt-access" not in row.access_token_ciphertext and "tok-tt-refresh" not in row.refresh_token_ciphertext

url, query = authorize("facebook")
assert url.startswith("https://www.facebook.com/v26.0/dialog/oauth?") and \
       query["scope"] == ["pages_show_list,pages_read_engagement,pages_manage_posts"], url
done = client.post("/api/channels/facebook/callback", json={"state": query["state"][0], "code": "code-2"})
assert done.status_code == 200, done.text
facebook = done.json()
assert (facebook["status"], facebook["reason"]) == ("authorization_required", "page_required"), facebook
assert facebook["pages"] == [{"id": "111", "name": "Page A"}, {"id": "222", "name": "Page B"}], facebook
no_secrets(done.text)
assert client.put("/api/channels/facebook/page", json={"page_id": "333"}).status_code == 409  # cannot publish there
chosen = client.put("/api/channels/facebook/page", json={"page_id": "222"})
assert chosen.status_code == 200 and (chosen.json()["status"], chosen.json()["account_name"]) == ("connected", "Page B"), chosen.text
listed = client.get("/api/channels")
no_secrets(listed.text)
assert {c["channel"]: c["status"] for c in listed.json()["channels"]} == \
       {"youtube": "not_connected", "tiktok": "connected", "facebook": "connected"}

# An expiring TikTok token is refreshed (and the rotated refresh token stored) before use.
with Session.begin() as db:
    db.get(channel_oauth.ChannelConnection, (workspace, "tiktok")).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
config = channel_oauth.ChannelConfig.from_environment("tiktok")
with Session() as db, httpx.Client(transport=httpx.MockTransport(platform)) as mock:
    token, scopes = channel_oauth.tiktok_access_token(db, config, workspace_id=workspace, client=mock)
assert token == "tok-tt-access-2" and "video.upload" in scopes
with Session() as db:
    row = db.get(channel_oauth.ChannelConnection, (workspace, "tiktok"))
    assert Fernet(os.environ["REELFORGE_TOKEN_ENCRYPTION_KEY"].encode()).decrypt(row.refresh_token_ciphertext.encode()) == b"tok-tt-refresh-2"
# Expired refresh tokens and missing scopes need the owner again.
with Session.begin() as db:
    db.get(channel_oauth.ChannelConnection, (workspace, "tiktok")).refresh_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
tiktok_status = next(c for c in client.get("/api/channels").json()["channels"] if c["channel"] == "tiktok")
assert (tiktok_status["status"], tiktok_status["reason"]) == ("authorization_required", "expired"), tiktok_status

# Only the owner starts or ends a connection; any member sees the status.
assert client.delete("/api/channels/tiktok").status_code == 204
assert next(c for c in client.get("/api/channels").json()["channels"] if c["channel"] == "tiktok")["status"] == "not_connected"
print("oauth ok")
'''

PUBLISH = r'''
from app import social_worker, youtube_worker
connect_youtube()
connect_channel("tiktok")
connect_channel("facebook")
run_id, asset_id = approved_run()

# Every target is validated against its own channel before anything is created.
bad = publish(run_id, target("youtube"), target("tiktok", privacy_status="public"))
assert bad.status_code == 422 and bad.json()["detail"]["channel"] == "tiktok", bad.text
bad = publish(run_id, target("facebook", description="x" * 4990, tags=["a" * 20]))
assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_description", bad.text
assert publish(run_id, target("youtube"), target("youtube")).status_code == 422
with Session() as db:
    assert db.scalar(select(func.count()).select_from(Publication)) == 0

# TikTok and Facebook tags are hashtags: one word each (the publish dialog joins the words for you).
bad = publish(run_id, target("tiktok", tags=["rừng đêm"]))
assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_tags", bad.text
created = publish(run_id, target("youtube"), target("tiktok", description="Xem đến cuối nhé!", tags=["rừngđêm", "cú"]),
                  target("facebook"), status=201).json()["publications"]
assert [p["channel"] for p in created] == ["youtube", "tiktok", "facebook"]
assert all(p["state"] == "queued" and p["can_cancel"] for p in created), created
assert created[1]["tags"] == ["rừngđêm", "cú"]
with Session() as db:
    keys = sorted(db.scalars(select(WorkflowJob.logical_key).where(WorkflowJob.logical_key.like("publish:%"))))
assert keys == sorted(f"publish:{channel}:{run_id}" for channel in ("youtube", "tiktok", "facebook")), keys
again = publish(run_id, target("youtube"), target("tiktok", description="Xem đến cuối nhé!", tags=["rừngđêm", "cú"]),
                target("facebook"))
assert again.status_code == 200 and [p["id"] for p in again.json()["publications"]] == [p["id"] for p in created]
summary = client.get(f"/api/workflow-runs/{run_id}/summary").json()["publishing"]
assert [p["channel"] for p in summary["publications"]] == ["youtube", "tiktok", "facebook"]
assert {c["channel"]: c["status"] for c in summary["channels"]}["tiktok"] == "connected"

seen = []
def platforms(request):
    seen.append(request)
    url = str(request.url)
    if url.startswith("https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"):
        assert request.headers["Authorization"] == "Bearer tok-tiktok-access"
        size = json.loads(request.content)["source_info"]["video_size"]
        assert size == len(VALID_MP4)
        return httpx.Response(200, json={"data": {"publish_id": "v_inbox_file~v2.123",
                                                  "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1&upload_token=t"},
                                         "error": {"code": "ok", "message": ""}})
    if url.startswith("https://open-upload.tiktokapis.com/video/"):
        assert request.headers["Content-Range"] == f"bytes 0-{len(VALID_MP4) - 1}/{len(VALID_MP4)}"
        return httpx.Response(201)
    if url == "https://open.tiktokapis.com/v2/post/publish/status/fetch/":
        polls = sum(1 for r in seen if str(r.url) == url)
        status = "PROCESSING_UPLOAD" if polls == 1 else "SEND_TO_USER_INBOX"
        return httpx.Response(200, json={"data": {"status": status}, "error": {"code": "ok"}})
    if url == "https://graph.facebook.com/v26.0/me/video_reels":
        assert request.headers["Authorization"] == "Bearer tok-facebook-access"
        body = parse_qs(request.content.decode())
        if body["upload_phase"] == ["start"]:
            return httpx.Response(200, json={"video_id": "987654321",
                                             "upload_url": "https://rupload.facebook.com/video-upload/v26.0/987654321"})
        assert body["video_state"] == ["PUBLISHED"] and body["description"] == ["Một đêm yên tĩnh.\n\n#rừng"], body
        return httpx.Response(200, json={"success": True})
    if url == "https://rupload.facebook.com/video-upload/v26.0/987654321":
        assert request.content == VALID_MP4
        return httpx.Response(200, json={"success": True})
    if url.startswith("https://graph.facebook.com/v26.0/987654321?fields=status"):
        return httpx.Response(200, json={"status": {"video_status": "ready", "uploading_phase": {"status": "complete"},
                                                    "processing_phase": {"status": "complete"},
                                                    "publishing_phase": {"status": "complete"}}})
    raise AssertionError(url)

from urllib.parse import parse_qs
with httpx.Client(transport=httpx.MockTransport(platforms)) as mock:
    drain(social_worker, client=mock, poll_seconds=0)
tiktok, facebook = publication(created[1]["id"]), publication(created[2]["id"])
# TikTok received a draft: succeeded, but not published until the creator posts it in the app.
assert (tiktok["state"], tiktok["remote_status"], tiktok["published_at"], tiktok["url"]) == \
       ("succeeded", "sent_to_inbox", None, None), tiktok
assert (facebook["state"], facebook["remote_status"], facebook["remote_id"]) == ("succeeded", "published", "987654321"), facebook
assert facebook["published_at"] and facebook["url"] == "https://www.facebook.com/reel/987654321"
# The social worker never touched the YouTube job: each channel has its own queue and worker.
assert publication(created[0]["id"])["state"] == "queued"
with Session() as db:
    assert not list((media_root(db) / ".publish-tmp").glob("*"))
    ciphertexts = [p.upload_session_ciphertext for p in db.scalars(select(Publication))]
assert ciphertexts == [None, None, None]
no_secrets(client.get("/api/publications").text)

# A second run: TikTok's answer to the upload request is lost, Facebook publishes a draft.
run2, _ = approved_run()
created = publish(run2, target("tiktok"), target("facebook", privacy_status="private"), status=201).json()["publications"]
def second(request):
    url = str(request.url)
    if "inbox/video/init" in url:
        return httpx.Response(502)
    if url == "https://graph.facebook.com/v26.0/me/video_reels":
        body = parse_qs(request.content.decode())
        if body["upload_phase"] == ["start"]:
            return httpx.Response(200, json={"video_id": "55", "upload_url": "https://rupload.facebook.com/video-upload/v26.0/55"})
        assert body["video_state"] == ["DRAFT"]
        return httpx.Response(200, json={"success": True})
    if url.startswith("https://rupload.facebook.com/"):
        return httpx.Response(200, json={"success": True})
    if "fields=status" in url:
        return httpx.Response(200, json={"status": {"video_status": "ready", "processing_phase": {"status": "complete"},
                                                    "publishing_phase": {"status": "not_started"}}})
    raise AssertionError(url)
with httpx.Client(transport=httpx.MockTransport(second)) as mock:
    drain(social_worker, client=mock, poll_seconds=0)
tiktok, facebook = publication(created[0]["id"]), publication(created[1]["id"])
# Flagged for a look, but retryable: no video byte was sent, so a retry cannot post twice.
assert (tiktok["state"], tiktok["last_error"], tiktok["can_retry"]) == ("needs_attention", "start:submission_unknown", True), tiktok
assert (facebook["state"], facebook["remote_status"], facebook["published_at"]) == ("succeeded", "draft", None), facebook

# A third run: TikTok reports the upload failed (nothing exists there), so a retry is allowed.
run3, _ = approved_run()
created = publish(run3, target("tiktok"), status=201).json()["publications"]
def failing(request):
    url = str(request.url)
    if "inbox/video/init" in url:
        return httpx.Response(200, json={"data": {"publish_id": "p9", "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=9&upload_token=t"},
                                         "error": {"code": "ok"}})
    if url.startswith("https://open-upload.tiktokapis.com/"):
        return httpx.Response(201)
    return httpx.Response(200, json={"data": {"status": "FAILED", "fail_reason": "file_format_check_failed"},
                                     "error": {"code": "ok"}})
with httpx.Client(transport=httpx.MockTransport(failing)) as mock:
    drain(social_worker, client=mock, poll_seconds=0)
failed = publication(created[0]["id"])
assert (failed["state"], failed["last_error"], failed["can_retry"]) == \
       ("failed", "remote_failed:file_format_check_failed", True), failed
retried = client.post(f"/api/publications/{failed['id']}/retry", json={"title": "Bản mới"})
assert retried.status_code == 202 and retried.json()["state"] == "queued", retried.text
with Session() as db:
    assert db.scalar(select(func.count()).select_from(WorkflowJob).where(
        WorkflowJob.logical_key.like(f"publish:tiktok:{run3}:retry:%"))) == 1

# A disconnected channel is refused up front, naming the channel.
assert client.delete("/api/channels/facebook").status_code == 204
run4, _ = approved_run()
refused = publish(run4, target("youtube"), target("facebook"))
assert refused.status_code == 409 and refused.json()["detail"]["channel"] == "facebook", refused.text
# A connection that changed after queueing stops the upload for a manual check.
connect_channel("facebook")
queued = publish(run4, target("facebook"), status=201).json()["publications"][0]
connect_channel("facebook", account="Trang khác")
with Session.begin() as db:
    db.get(channel_oauth.ChannelConnection, (workspace, "facebook")).connected_at = datetime.now(timezone.utc) + timedelta(seconds=5)
with httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError(str(r.url))))) as mock:
    drain(social_worker, client=mock, poll_seconds=0)
assert publication(queued["id"])["last_error"] == "connection_changed"
print("publish ok")
'''


class MultiPlatformPublishingTest(unittest.TestCase):
    def check(self, body):
        completed = run_program(body)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])

    def test_tiktok_and_facebook_oauth_status_and_tokens(self):
        self.check(OAUTH)

    def test_publishing_to_three_channels_with_independent_uploads(self):
        self.check(PUBLISH)


if __name__ == "__main__":
    unittest.main()
