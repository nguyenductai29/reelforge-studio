"""Contract tests for private YouTube resumable uploads."""

import json
from pathlib import Path
import tempfile
import unittest

import httpx

from app.publishers.youtube import (
    UploadSession,
    YouTubeUploadError,
    YouTubeUploadRequest,
    start_private_upload,
    upload_private_video,
)


SESSION_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&upload_id=abc123&part=snippet,status"
)


class YouTubeUploadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.video = Path(self.directory.name) / "clip.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 262139)
        self.request = YouTubeUploadRequest(
            file_path=self.video,
            title="A generated story",
            description="A short film",
        )

    def test_initiate_and_upload_private_video_in_bounded_chunks(self):
        seen = []

        def handle(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(200, headers={"Location": SESSION_URL})
            if len(seen) == 2:
                return httpx.Response(308, headers={"Range": "bytes=0-262143"})
            return httpx.Response(
                201,
                json={"id": "YT123abc_45", "status": {"privacyStatus": "private", "uploadStatus": "uploaded"}},
            )

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            result = upload_private_video(self.request, "oauth-token", client=client, chunk_size=262144)

        self.assertEqual(result.video_id, "YT123abc_45")
        self.assertEqual(result.privacy_status, "private")
        self.assertEqual(result.upload_status, "uploaded")
        self.assertEqual(len(seen), 3)
        start, first, last = seen
        self.assertEqual(start.method, "POST")
        self.assertEqual(start.url.path, "/upload/youtube/v3/videos")
        self.assertEqual(start.url.params["uploadType"], "resumable")
        self.assertEqual(start.url.params["part"], "snippet,status")
        self.assertEqual(start.headers["Authorization"], "Bearer oauth-token")
        self.assertEqual(start.headers["X-Upload-Content-Length"], "262151")
        self.assertEqual(start.headers["X-Upload-Content-Type"], "video/mp4")
        metadata = json.loads(start.content)
        self.assertEqual(metadata["snippet"], {"title": "A generated story", "description": "A short film"})
        self.assertEqual(metadata["status"], {"privacyStatus": "private", "containsSyntheticMedia": True})
        self.assertEqual(first.headers["Content-Range"], "bytes 0-262143/262151")
        self.assertEqual(len(first.content), 262144)
        self.assertEqual(last.headers["Content-Range"], "bytes 262144-262150/262151")
        self.assertEqual(len(last.content), 7)

    def test_rejects_unsafe_session_location_before_sending_oauth_token(self):
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, headers={"Location": "https://127.0.0.1/steal"})

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(YouTubeUploadError) as raised:
                upload_private_video(self.request, "oauth-token", client=client)

        self.assertEqual(raised.exception.code, "unsafe_session_url")
        self.assertEqual(len(seen), 1)

    def test_resumes_from_confirmed_byte_after_interrupted_attempt(self):
        ranges = []

        def handle(request):
            ranges.append(request.headers["Content-Range"])
            if len(ranges) == 1:
                return httpx.Response(308, headers={"Range": "bytes=0-262143"})
            return httpx.Response(201, json={"id": "YT123abc_45", "status": {"privacyStatus": "private"}})

        session = UploadSession(url=SESSION_URL, file_size=262151)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            result = upload_private_video(self.request, "oauth-token", client=client, session=session, chunk_size=262144)

        self.assertEqual(ranges, ["bytes */262151", "bytes 262144-262150/262151"])
        self.assertEqual(result.video_id, "YT123abc_45")

    def test_validates_metadata_and_file_before_initiating_session(self):
        seen = []
        with httpx.Client(transport=httpx.MockTransport(lambda request: seen.append(request))) as client:
            for request in (
                YouTubeUploadRequest(self.video, "  "),
                YouTubeUploadRequest(self.video.with_suffix(".avi"), "Title"),
            ):
                with self.subTest(request=request):
                    with self.assertRaises(YouTubeUploadError) as raised:
                        start_private_upload(request, "oauth-token", client=client)
                    self.assertEqual(raised.exception.code, "invalid_upload")
        self.assertEqual(seen, [])

    def test_accepts_extensionless_generated_asset_when_mp4_signature_is_valid(self):
        asset = Path(self.directory.name) / "e91c3391-f8e1-4b86-a80e-b2b60067ed39"
        asset.write_bytes(self.video.read_bytes())
        request = YouTubeUploadRequest(asset, "Generated video")
        seen = []

        def handle(http_request):
            seen.append(http_request)
            return httpx.Response(200, headers={"Location": SESSION_URL})

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            session = start_private_upload(request, "oauth-token", client=client)

        self.assertEqual(session, UploadSession(SESSION_URL, 262151))
        self.assertEqual(len(seen), 1)

    def test_server_error_exposes_session_for_scheduled_resume(self):
        seen = []

        def handle(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(200, headers={"Location": SESSION_URL})
            return httpx.Response(503)

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(YouTubeUploadError) as raised:
                upload_private_video(self.request, "oauth-token", client=client)

        self.assertEqual(raised.exception.code, "youtube_unavailable")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.session, UploadSession(SESSION_URL, 262151))
        self.assertEqual(len(seen), 2)

    def test_does_not_report_rejected_video_as_uploaded(self):
        def handle(request):
            if request.method == "POST":
                return httpx.Response(200, headers={"Location": SESSION_URL})
            return httpx.Response(
                201,
                json={"id": "YT123abc_45", "status": {"privacyStatus": "private", "uploadStatus": "rejected"}},
            )

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(YouTubeUploadError) as raised:
                upload_private_video(self.request, "oauth-token", client=client)

        self.assertEqual(raised.exception.code, "video_rejected")
        self.assertFalse(raised.exception.retryable)

    def test_rejects_unbounded_chunk_size_before_network(self):
        seen = []
        def handle(request):
            seen.append(request)
            return httpx.Response(200, headers={"Location": SESSION_URL})

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(YouTubeUploadError) as raised:
                upload_private_video(self.request, "oauth-token", client=client, chunk_size=64 * 1024 * 1024)
        self.assertEqual(raised.exception.code, "invalid_upload")
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
