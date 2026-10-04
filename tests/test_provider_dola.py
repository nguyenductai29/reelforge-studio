"""The Dola gateway adapter uses only its HTTP queue and result contract."""

import json
import os
import unittest
from unittest.mock import patch

import httpx

try:
    from app.providers import dola
except ImportError:
    dola = None


BASE = "http://127.0.0.1:8000"
TASK_ID = "video_1234567890abcdef1234567890abcdef"
TASK_URL = f"{BASE}/v1/videos/{TASK_ID}"
VIDEO_URL = f"{BASE}/videos/render_abc123.mp4"


class DolaClientTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(dola, "Dola provider adapter is missing")

    def test_submit_maps_one_clip_to_gateway_contract_and_returns_persistable_handle(self):
        def respond(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), f"{BASE}/v1/videos/generations")
            self.assertEqual(request.headers["Authorization"], "Bearer secret")
            self.assertEqual(json.loads(request.content), {
                "model": "seedance-2.5", "prompt": "A fox walks in the rain",
                "ratio": "9:16", "duration": 10, "reference_images": [],
            })
            return httpx.Response(200, json={
                "id": TASK_ID, "status": "queued", "model": "seedance-2.5",
                "prompt": "A fox walks in the rain", "video_url": None, "error": None,
            })

        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = dola.DolaClient("secret", base_url=BASE, http_client=http)
            submission = client.submit(dola.VideoRequest(
                "seedance-2.5", "A fox walks in the rain", aspect_ratio="9:16",
                duration="10s",
            ))

        self.assertEqual(submission.model_id, "seedance-2.5")
        self.assertEqual(submission.request_id, TASK_ID)
        self.assertEqual(submission.status_url, TASK_URL)
        self.assertEqual(submission.response_url, TASK_URL)

    def test_status_maps_documented_queue_lifecycle_and_failure(self):
        replies = iter([
            {"id": TASK_ID, "status": "queued", "model": "seedance-2.0",
             "prompt": "fox", "video_url": None, "error": None},
            {"id": TASK_ID, "status": "processing", "model": "seedance-2.0",
             "prompt": "fox", "video_url": None, "error": None},
            {"id": TASK_ID, "status": "completed", "model": "seedance-2.0",
             "prompt": "fox", "video_url": VIDEO_URL, "error": None},
            {"id": TASK_ID, "status": "failed", "model": "seedance-2.0",
             "prompt": "fox", "video_url": None, "error": "No account in pool"},
        ])

        def respond(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(str(request.url), TASK_URL)
            self.assertEqual(request.headers["Authorization"], "Bearer secret")
            return httpx.Response(200, json=next(replies))

        submission = dola.Submission("seedance-2.0", TASK_ID, TASK_URL, TASK_URL)
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = dola.DolaClient("secret", base_url=BASE, http_client=http)
            states = [client.status(submission) for _ in range(4)]

        self.assertEqual([state.state for state in states],
                         ["queued", "running", "completed", "failed"])
        self.assertEqual(states[3].error.code, "provider_failed")
        self.assertFalse(states[3].error.retryable)

    def test_result_requires_completion_and_media_on_operator_configured_origin(self):
        def respond(request):
            return httpx.Response(200, json={
                "id": TASK_ID, "status": "completed", "model": "seedance-2.0",
                "prompt": "fox", "video_url": VIDEO_URL, "error": None,
            })

        submission = dola.Submission("seedance-2.0", TASK_ID, TASK_URL, TASK_URL)
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            result = dola.DolaClient("secret", base_url=BASE, http_client=http).result(submission)
        self.assertEqual(result.video_url, VIDEO_URL)

        with patch.dict(os.environ, {"DOLA_BASE_URL": BASE}, clear=False):
            dola.validate_media_url(VIDEO_URL)

    def test_rejects_unsupported_model_and_controls_before_network(self):
        def fail_on_request(request):
            self.fail(f"Unexpected network request: {request.url}")

        requests = [
            (dola.VideoRequest("other-model", "fox"), "unsupported_model"),
            (dola.VideoRequest("seedance-2.0", "   "), "invalid_request"),
            (dola.VideoRequest("seedance-2.0", "fox", duration="8s"), "invalid_request"),
            (dola.VideoRequest("seedance-2.0", "fox", aspect_ratio="2:3"), "invalid_request"),
            (dola.VideoRequest("seedance-2.0", "fox", resolution="1080p"), "invalid_request"),
            (dola.VideoRequest("seedance-2.0", "fox", generate_audio=False), "invalid_request"),
        ]
        with httpx.Client(transport=httpx.MockTransport(fail_on_request)) as http:
            client = dola.DolaClient("secret", base_url=BASE, http_client=http)
            for request, code in requests:
                with self.subTest(request=request), self.assertRaises(dola.ProviderError) as raised:
                    client.submit(request)
                self.assertEqual(raised.exception.code, code)

    def test_rejects_unsafe_gateway_configuration_and_bearer_value(self):
        invalid_bases = [
            "http://example.test", "http://127.0.0.1:8000@evil.test",
            "http://127.0.0.1:8000/redirect", "https://example.test/path",
            "https://example.test?next=127.0.0.1", "file:///tmp/gateway",
        ]
        for base in invalid_bases:
            with self.subTest(base=base), self.assertRaises(dola.ProviderError) as raised:
                dola.DolaClient("secret", base_url=base)
            self.assertEqual(raised.exception.code, "invalid_config")
        with self.assertRaises(dola.ProviderError) as raised:
            dola.DolaClient("secret\r\nX-Injected: yes", base_url=BASE)
        self.assertEqual(raised.exception.code, "invalid_config")

    def test_revalidates_persisted_task_handle_before_polling(self):
        def fail_on_request(request):
            self.fail(f"Unexpected network request: {request.url}")

        bad_handles = [
            dola.Submission("seedance-2.0", TASK_ID,
                            "http://169.254.169.254/latest/meta-data", TASK_URL),
            dola.Submission("seedance-2.0", TASK_ID, TASK_URL,
                            "https://evil.test/video"),
            dola.Submission("seedance-2.0", "../other", TASK_URL, TASK_URL),
            dola.Submission("other-model", TASK_ID, TASK_URL, TASK_URL),
        ]
        with httpx.Client(transport=httpx.MockTransport(fail_on_request)) as http:
            client = dola.DolaClient("secret", base_url=BASE, http_client=http)
            for handle in bad_handles:
                with self.subTest(handle=handle), self.assertRaises(dola.ProviderError) as raised:
                    client.status(handle)
                self.assertEqual(raised.exception.code, "unsafe_url")

    def test_rejects_media_url_off_configured_origin_or_outside_video_path(self):
        bad_urls = [
            "http://169.254.169.254/latest/meta-data",
            f"{BASE}@evil.test/videos/render.mp4",
            f"{BASE}/videos/../private.mp4",
            f"{BASE}/videos/render.mp4?next=evil",
            f"{BASE}/videos/render.txt",
            f"{BASE}/videos/%2e%2e%2fprivate.mp4",
        ]
        submission = dola.Submission("seedance-2.0", TASK_ID, TASK_URL, TASK_URL)
        for video_url in bad_urls:
            with self.subTest(video_url=video_url):
                def respond(request):
                    return httpx.Response(200, json={
                        "id": TASK_ID, "status": "completed", "model": "seedance-2.0",
                        "prompt": "fox", "video_url": video_url, "error": None,
                    })

                with httpx.Client(transport=httpx.MockTransport(respond)) as http:
                    with self.assertRaises(dola.ProviderError) as raised:
                        dola.DolaClient("secret", base_url=BASE, http_client=http).result(submission)
                self.assertEqual(raised.exception.code, "unsafe_url")

    def test_timeout_on_submit_is_indeterminate_but_poll_can_retry(self):
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        submission = dola.Submission("seedance-2.0", TASK_ID, TASK_URL, TASK_URL)
        with httpx.Client(transport=httpx.MockTransport(timeout)) as http:
            client = dola.DolaClient("secret", base_url=BASE, http_client=http, timeout_seconds=2.5)
            with self.assertRaises(dola.ProviderError) as raised:
                client.submit(dola.VideoRequest("seedance-2.0", "fox"))
            self.assertEqual(raised.exception.code, "submission_unknown")
            self.assertFalse(raised.exception.retryable)
            with self.assertRaises(dola.ProviderError) as raised:
                client.status(submission)
            self.assertEqual(raised.exception.code, "timeout")
            self.assertTrue(raised.exception.retryable)

    def test_http_redirect_cannot_forward_bearer_token(self):
        def respond(request):
            return httpx.Response(302, headers={"location": "http://evil.test/steal"})

        with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as http:
            with self.assertRaises(dola.ProviderError) as raised:
                dola.DolaClient("secret", base_url=BASE, http_client=http).submit(
                    dola.VideoRequest("seedance-2.0", "fox"))
        self.assertEqual(raised.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
