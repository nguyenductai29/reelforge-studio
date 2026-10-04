"""Contract checks for the official Meta Page Reels upload flow."""

import tempfile
import unittest
from pathlib import Path

import httpx

from app.publishers.facebook import (
    FacebookReelError,
    FacebookReelRequest,
    ReelSession,
    get_reel_status,
    publish_reel,
    start_reel_upload,
    upload_reel_file,
)


VIDEO_ID = "724627979033843"
UPLOAD_URL = f"https://rupload.facebook.com/video-upload/v26.0/{VIDEO_ID}"
TOKEN = "secret-page-token"


class FacebookReelsTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "clip.mp4"
        self.content = b"\x00\x00\x00\x18ftypmp42" + b"video-content" * 48
        self.path.write_bytes(self.content)
        self.request = FacebookReelRequest(self.path, "A story", "Caption")

    def client(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10)
        self.addCleanup(client.close)
        return client

    def test_start_upload_status_and_finish_follow_meta_contract(self):
        calls = []

        def handle(request):
            calls.append((request.method, str(request.url)))
            self.assertNotIn(TOKEN, str(request.url))
            if len(calls) == 1:
                self.assertEqual(str(request.url), "https://graph.facebook.com/v26.0/me/video_reels")
                self.assertEqual(request.headers["authorization"], f"Bearer {TOKEN}")
                self.assertEqual(request.content, b"upload_phase=start")
                return httpx.Response(200, json={"video_id": VIDEO_ID, "upload_url": UPLOAD_URL})
            if len(calls) == 2:
                self.assertEqual(str(request.url), UPLOAD_URL)
                self.assertEqual(request.headers["authorization"], f"OAuth {TOKEN}")
                self.assertEqual(request.headers["offset"], "0")
                self.assertEqual(request.headers["file_size"], str(len(self.content)))
                self.assertEqual(request.headers["content-type"], "application/octet-stream")
                self.assertEqual(request.content, self.content)
                return httpx.Response(200, json={"success": True})
            if len(calls) == 3:
                self.assertEqual(request.method, "GET")
                self.assertEqual(str(request.url), f"https://graph.facebook.com/v26.0/{VIDEO_ID}?fields=status")
                self.assertEqual(request.headers["authorization"], f"Bearer {TOKEN}")
                return httpx.Response(200, json={"status": {"video_status": "processing", "uploading_phase": {"status": "complete"}, "processing_phase": {"status": "not_started"}, "publishing_phase": {"status": "not_started"}}})
            self.assertEqual(str(request.url), "https://graph.facebook.com/v26.0/me/video_reels")
            self.assertEqual(request.headers["authorization"], f"Bearer {TOKEN}")
            self.assertEqual(request.headers["content-type"], "application/x-www-form-urlencoded")
            self.assertEqual(dict(httpx.QueryParams(request.content.decode())), {
                "video_id": VIDEO_ID,
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "title": "A story",
                "description": "Caption",
            })
            return httpx.Response(200, json={"success": True})

        client = self.client(handle)
        session = start_reel_upload(self.request, TOKEN, client=client)
        self.assertEqual(session, ReelSession(VIDEO_ID, UPLOAD_URL, len(self.content)))
        self.assertTrue(upload_reel_file(self.request, session, TOKEN, client=client))
        status = get_reel_status(session, TOKEN, client=client)
        self.assertEqual(status.video_status, "processing")
        self.assertEqual(status.uploading_status, "complete")
        result = publish_reel(self.request, session, TOKEN, client=client)
        self.assertEqual(result.video_id, VIDEO_ID)
        self.assertTrue(result.accepted)
        self.assertEqual(len(calls), 4)

    def test_start_rejects_attacker_controlled_upload_url(self):
        for bad_url in (
            f"https://evil.example/video-upload/v26.0/{VIDEO_ID}",
            f"http://rupload.facebook.com/video-upload/v26.0/{VIDEO_ID}",
            f"https://rupload.facebook.com.evil.example/video-upload/v26.0/{VIDEO_ID}",
            f"https://rupload.facebook.com/video-upload/v26.0/{VIDEO_ID}?access_token=leak",
            f"https://rupload.facebook.com/video-upload/v26.0/{VIDEO_ID}#fragment",
            f"https://rupload.facebook.com:444/video-upload/v26.0/{VIDEO_ID}",
        ):
            with self.subTest(url=bad_url):
                client = self.client(lambda _: httpx.Response(200, json={"video_id": VIDEO_ID, "upload_url": bad_url}))
                with self.assertRaises(FacebookReelError) as raised:
                    start_reel_upload(self.request, TOKEN, client=client)
                self.assertEqual(raised.exception.code, "unsafe_upload_url")

    def test_upload_rejects_mutated_or_invalid_session_before_network(self):
        client = self.client(lambda _: self.fail("network should not be called"))
        sessions = [
            ReelSession("not-numeric", UPLOAD_URL, len(self.content)),
            ReelSession(VIDEO_ID, "https://127.0.0.1/upload", len(self.content)),
            ReelSession(VIDEO_ID, UPLOAD_URL, len(self.content) + 1),
        ]
        for session in sessions:
            with self.subTest(session=session), self.assertRaises(FacebookReelError):
                upload_reel_file(self.request, session, TOKEN, client=client)

    def test_validation_rejects_bad_file_and_token_before_network(self):
        client = self.client(lambda _: self.fail("network should not be called"))
        with self.assertRaises(FacebookReelError) as raised:
            start_reel_upload(self.request, "bad\r\ntoken", client=client)
        self.assertEqual(raised.exception.code, "invalid_token")
        self.path.write_bytes(b"not an mp4")
        with self.assertRaises(FacebookReelError) as raised:
            start_reel_upload(self.request, TOKEN, client=client)
        self.assertEqual(raised.exception.code, "invalid_upload")

    def test_rejects_redirect_without_following_it_or_leaking_token(self):
        seen = []

        def handle(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

        client = self.client(handle)
        with self.assertRaises(FacebookReelError) as raised:
            start_reel_upload(self.request, TOKEN, client=client)
        self.assertEqual(raised.exception.code, "unsafe_redirect")
        self.assertEqual(len(seen), 1)
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_meta_errors_are_safe_and_retryable_only_when_appropriate(self):
        cases = [
            (401, {"error": {"code": 190, "message": TOKEN}}, "authorization_error", False),
            (403, {"error": {"code": 200, "message": TOKEN}}, "authorization_error", False),
            (400, {"error": {"code": 613, "message": TOKEN}}, "rate_limited", True),
            (429, {}, "rate_limited", True),
            (500, {}, "meta_unavailable", True),
            (400, {}, "meta_error", False),
        ]
        for code, body, expected, retryable in cases:
            with self.subTest(code=code, body=body):
                client = self.client(lambda _: httpx.Response(code, json=body))
                with self.assertRaises(FacebookReelError) as raised:
                    start_reel_upload(self.request, TOKEN, client=client)
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(raised.exception.retryable, retryable)
                self.assertEqual(raised.exception.http_status, code)
                self.assertNotIn(TOKEN, str(raised.exception))

    def test_timeout_has_retryable_transport_code(self):
        def handle(request):
            raise httpx.ReadTimeout("timed out", request=request)

        client = self.client(handle)
        with self.assertRaises(FacebookReelError) as raised:
            start_reel_upload(self.request, TOKEN, client=client)
        self.assertEqual(raised.exception.code, "transport_error")
        self.assertTrue(raised.exception.retryable)

    def test_malformed_success_response_is_rejected(self):
        client = self.client(lambda _: httpx.Response(200, json={"video_id": VIDEO_ID, "upload_url": UPLOAD_URL.replace(VIDEO_ID, "123")}))
        with self.assertRaises(FacebookReelError) as raised:
            start_reel_upload(self.request, TOKEN, client=client)
        self.assertEqual(raised.exception.code, "unsafe_upload_url")

    def test_publish_failure_does_not_claim_publication(self):
        session = ReelSession(VIDEO_ID, UPLOAD_URL, len(self.content))
        client = self.client(lambda _: httpx.Response(200, json={"success": False}))
        with self.assertRaises(FacebookReelError) as raised:
            publish_reel(self.request, session, TOKEN, client=client)
        self.assertEqual(raised.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
