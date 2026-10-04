"""TikTok inbox video upload contracts with fake HTTP and local MP4 files."""

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

import httpx

try:
    from app.publishers.tiktok import (
        TikTokClient,
        TikTokError,
        UploadSession,
        plan_upload,
        validate_upload_url,
    )
except ImportError:
    TikTokClient = None


UPLOAD_URL = "https://open-upload.tiktokapis.com/video/?upload_id=12345&upload_token=Xza123"
PUBLISH_ID = "v_inbox_file~v2.123456789"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"video test bytes"


def ok(data):
    return httpx.Response(200, json={"data": data, "error": {"code": "ok", "message": "", "log_id": "test"}})


def upload_session(size=len(MP4)):
    return UploadSession(publish_id=PUBLISH_ID, upload_url=UPLOAD_URL,
                         video_size=size, chunk_size=size, total_chunk_count=1)


class TikTokPostingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(TikTokClient, "TikTok adapter is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "video.mp4"
        self.path.write_bytes(MP4)

    def client(self, transport, *, approved=frozenset({"video.upload"}), granted=frozenset({"video.upload"})):
        http_client = httpx.Client(transport=httpx.MockTransport(transport))
        self.addCleanup(http_client.close)
        return TikTokClient("access-token", approved_scopes=approved, granted_scopes=granted,
                            http_client=http_client)

    def test_draft_init_sends_only_file_upload_metadata_with_explicit_scope(self):
        sent = []

        def handle(request):
            sent.append(request)
            return ok({"publish_id": PUBLISH_ID, "upload_url": UPLOAD_URL})

        session = self.client(handle).init_draft_upload(self.path)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].method, "POST")
        self.assertEqual(str(sent[0].url), "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/")
        self.assertEqual(sent[0].headers["authorization"], "Bearer access-token")
        self.assertEqual(json.loads(sent[0].read()), {"source_info": {
            "source": "FILE_UPLOAD", "video_size": len(MP4),
            "chunk_size": len(MP4), "total_chunk_count": 1,
        }})
        self.assertEqual(session, upload_session())
        self.assertEqual(asdict(session)["publish_id"], PUBLISH_ID)

    def test_draft_init_requires_app_approval_and_user_grant_before_network(self):
        sent = []

        def handle(request):
            sent.append(request)
            return ok({"publish_id": PUBLISH_ID, "upload_url": UPLOAD_URL})

        for approved, granted in ((frozenset(), frozenset({"video.upload"})),
                                  (frozenset({"video.upload"}), frozenset())):
            with self.subTest(approved=approved, granted=granted), self.assertRaises(TikTokError):
                self.client(handle, approved=approved, granted=granted).init_draft_upload(self.path)
        self.assertEqual(sent, [])

    def test_upload_next_chunk_streams_mp4_without_oauth_token(self):
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(201)

        result = self.client(handle).upload_next_chunk(upload_session(), self.path)
        self.assertEqual(result.next_chunk_index, 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].method, "PUT")
        self.assertEqual(str(sent[0].url), UPLOAD_URL)
        self.assertEqual(sent[0].headers["content-type"], "video/mp4")
        self.assertEqual(sent[0].headers["content-range"], f"bytes 0-{len(MP4)-1}/{len(MP4)}")
        self.assertEqual(sent[0].headers["content-length"], str(len(MP4)))
        self.assertNotIn("authorization", sent[0].headers)
        self.assertEqual(sent[0].read(), MP4)

    def test_upload_plan_uses_multiple_legal_chunks_above_64_mb(self):
        size = 64_000_001
        plan = plan_upload(size)
        self.assertGreaterEqual(plan.total_chunk_count, 2)
        self.assertEqual(plan.total_chunk_count, size // plan.chunk_size)
        self.assertGreaterEqual(plan.chunk_size, 5_000_000)
        self.assertLessEqual(plan.chunk_size, 64_000_000)
        final_size = size - plan.chunk_size * (plan.total_chunk_count - 1)
        self.assertLessEqual(final_size, 128_000_000)

    def test_upload_url_rejects_untrusted_host_and_missing_token(self):
        for bad_url in (
            "http://open-upload.tiktokapis.com/video/?upload_id=123&upload_token=abc",
            "https://open-upload.tiktokapis.com.evil.test/video/?upload_id=123&upload_token=abc",
            "https://open-upload.tiktokapis.com@evil.test/video/?upload_id=123&upload_token=abc",
            "https://open-upload.tiktokapis.com:443/video/?upload_id=123&upload_token=abc",
            "https://open-upload.tiktokapis.com/video/?upload_id=123",
            "https://open-upload.tiktokapis.com/redirect/?upload_id=123&upload_token=abc",
        ):
            with self.subTest(bad_url=bad_url), self.assertRaises(TikTokError):
                validate_upload_url(bad_url)
        validate_upload_url(UPLOAD_URL)
        validate_upload_url("https://upload.us.tiktokapis.com/video/?upload_id=123&upload_token=abc")

    def test_persisted_upload_url_is_checked_before_put(self):
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(201)

        forged = UploadSession(publish_id=PUBLISH_ID, upload_url="https://evil.test/steal",
                               video_size=len(MP4), chunk_size=len(MP4), total_chunk_count=1)
        with self.assertRaises(TikTokError):
            self.client(handle).upload_next_chunk(forged, self.path)
        self.assertEqual(sent, [])

    def test_status_distinguishes_inbox_delivery_from_creator_publication(self):
        states = iter(["PROCESSING_UPLOAD", "SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"])
        sent = []

        def handle(request):
            sent.append(request)
            state = next(states)
            return ok({"status": state, "uploaded_bytes": len(MP4), "fail_reason": "invalid_video" if state == "FAILED" else ""})

        client = self.client(handle)
        self.assertEqual(client.fetch_status(PUBLISH_ID).state, "uploading")
        self.assertEqual(client.fetch_status(PUBLISH_ID).state, "awaiting_creator")
        self.assertEqual(client.fetch_status(PUBLISH_ID).state, "published")
        failed = client.fetch_status(PUBLISH_ID)
        self.assertEqual((failed.state, failed.fail_reason), ("failed", "invalid_video"))
        for request in sent:
            self.assertEqual(str(request.url), "https://open.tiktokapis.com/v2/post/publish/status/fetch/")
            self.assertIn(PUBLISH_ID.encode(), request.read())

    def test_api_error_code_matters_even_when_http_is_200(self):
        def handle(_request):
            return httpx.Response(200, json={"data": {}, "error": {"code": "scope_not_authorized", "message": "Denied"}})

        with self.assertRaises(TikTokError) as raised:
            self.client(handle).init_draft_upload(self.path)
        self.assertEqual(raised.exception.code, "scope_not_authorized")

    def test_upload_failure_does_not_advance_persisted_session(self):
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(503)

        original = upload_session()
        with self.assertRaises(TikTokError) as raised:
            self.client(handle).upload_next_chunk(original, self.path)
        self.assertEqual(raised.exception.code, "upload_unknown")
        self.assertEqual(original.next_chunk_index, 0)
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
