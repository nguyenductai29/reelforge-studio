"""fal queue adapter contract, exercised without external requests."""

import json
import unittest

import httpx

import app.providers.fal as fal
from app.providers.fal import FalQueueClient, ProviderError, Submission, VideoRequest


MODEL = "fal-ai/veo3.1/fast"
REQUEST_ID = "764cabcf-b745-4b3e-ae38-1200304cf45b"
BASE = f"https://queue.fal.run/{MODEL}/requests/{REQUEST_ID}"


class FalQueueClientTest(unittest.TestCase):
    def test_submit_posts_documented_model_fields_and_returns_persistable_handle(self):
        def respond(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), f"https://queue.fal.run/{MODEL}")
            self.assertEqual(request.headers["Authorization"], "Key test-key")
            self.assertEqual(json.loads(request.content), {
                "prompt": "A fox runs through snow",
                "aspect_ratio": "9:16",
                "duration": "6s",
                "resolution": "720p",
                "generate_audio": False,
            })
            return httpx.Response(200, json={
                "request_id": REQUEST_ID,
                "status_url": f"{BASE}/status",
                "response_url": f"{BASE}/response",
                "cancel_url": f"{BASE}/cancel",
                "queue_position": 0,
            })

        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = FalQueueClient("test-key", http_client=http)
            submission = client.submit(VideoRequest(
                model_id=MODEL,
                prompt="A fox runs through snow",
                aspect_ratio="9:16",
                duration="6s",
                resolution="720p",
                generate_audio=False,
            ))

        self.assertEqual(submission.model_id, MODEL)
        self.assertEqual(submission.request_id, REQUEST_ID)
        self.assertEqual(submission.status_url, f"{BASE}/status")
        self.assertEqual(submission.response_url, f"{BASE}/response")

    def test_submit_rejects_unsupported_model_and_capabilities_before_network(self):
        def fail_on_request(request):
            self.fail(f"Unexpected network request: {request.url}")

        invalid_requests = [
            (VideoRequest(model_id="fal-ai/veo3.1/fast/../../other", prompt="fox"), "unsupported_model"),
            (VideoRequest(model_id=MODEL, prompt="   "), "invalid_request"),
            (VideoRequest(model_id=MODEL, prompt="fox", duration="10s"), "invalid_request"),
            (VideoRequest(model_id=MODEL, prompt="fox", aspect_ratio="1:1"), "invalid_request"),
            (VideoRequest(model_id=MODEL, prompt="fox", resolution="480p"), "invalid_request"),
            (VideoRequest(model_id=MODEL, prompt="fox", generate_audio="false"), "invalid_request"),
            (VideoRequest(model_id=[], prompt="fox"), "unsupported_model"),
            (VideoRequest(model_id=MODEL, prompt="fox", duration=[]), "invalid_request"),
        ]
        with httpx.Client(transport=httpx.MockTransport(fail_on_request)) as http:
            client = FalQueueClient("test-key", http_client=http)
            for request, code in invalid_requests:
                with self.subTest(request=request), self.assertRaises(ProviderError) as raised:
                    client.submit(request)
                self.assertEqual(raised.exception.code, code)

    def test_client_context_closes_owned_http_client(self):
        with FalQueueClient("test-key") as client:
            http = client.http_client
            self.assertFalse(http.is_closed)
        self.assertTrue(http.is_closed)

    def test_status_maps_queue_lifecycle_and_completed_failure(self):
        replies = iter([
            {"status": "IN_QUEUE", "request_id": REQUEST_ID, "queue_position": 3,
             "response_url": f"{BASE}/response"},
            {"status": "IN_PROGRESS", "request_id": REQUEST_ID,
             "response_url": f"{BASE}/response", "logs": []},
            {"status": "COMPLETED", "request_id": REQUEST_ID,
             "response_url": f"{BASE}/response", "metrics": {"inference_time": 5.1}},
            {"status": "COMPLETED", "request_id": REQUEST_ID,
             "response_url": f"{BASE}/response", "error": "Runner timed out",
             "error_type": "request_timeout"},
        ])

        def respond(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(str(request.url), f"{BASE}/status")
            self.assertEqual(request.headers["Authorization"], "Key test-key")
            return httpx.Response(200, json=next(replies))

        handle = Submission(MODEL, REQUEST_ID, f"{BASE}/status", f"{BASE}/response")
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = FalQueueClient("test-key", http_client=http)
            statuses = [client.status(handle) for _ in range(4)]

        self.assertEqual([status.state for status in statuses], ["queued", "running", "completed", "failed"])
        self.assertEqual(statuses[0].queue_position, 3)
        self.assertEqual(statuses[3].error.code, "request_timeout")
        self.assertTrue(statuses[3].error.retryable)

    def test_result_gets_documented_video_url(self):
        def respond(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(str(request.url), f"{BASE}/response")
            return httpx.Response(200, json={
                "video": {
                    "url": "https://v3b.fal.media/files/b/example/output.mp4",
                    "content_type": "video/mp4",
                    "file_name": "output.mp4",
                }
            })

        handle = Submission(MODEL, REQUEST_ID, f"{BASE}/status", f"{BASE}/response")
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            video = FalQueueClient("test-key", http_client=http).result(handle)

        self.assertEqual(video.video_url, "https://v3b.fal.media/files/b/example/output.mp4")
        self.assertEqual(video.content_type, "video/mp4")

    def test_submit_rejects_provider_queue_links_outside_its_request(self):
        bad_urls = [
            "http://queue.fal.run/fal-ai/veo3.1/fast/requests/" + REQUEST_ID + "/status",
            "https://queue.fal.run.evil.test/fal-ai/veo3.1/fast/requests/" + REQUEST_ID + "/status",
            "https://queue.fal.run@evil.test/fal-ai/veo3.1/fast/requests/" + REQUEST_ID + "/status",
            "https://queue.fal.run:443/fal-ai/veo3.1/fast/requests/" + REQUEST_ID + "/status",
            f"{BASE}/status?next=http://127.0.0.1",
            f"https://queue.fal.run/fal-ai/other/requests/{REQUEST_ID}/status",
            f"{BASE}/../other/status",
        ]

        for bad_url in bad_urls:
            with self.subTest(url=bad_url):
                def respond(request):
                    return httpx.Response(200, json={
                        "request_id": REQUEST_ID,
                        "status_url": bad_url,
                        "response_url": f"{BASE}/response",
                    })

                with httpx.Client(transport=httpx.MockTransport(respond)) as http:
                    with self.assertRaises(ProviderError) as raised:
                        FalQueueClient("test-key", http_client=http).submit(VideoRequest(MODEL, "fox"))
                self.assertEqual(raised.exception.code, "unsafe_url")

    def test_persisted_queue_links_are_revalidated_before_fetch(self):
        def fail_on_request(request):
            self.fail(f"Unexpected network request: {request.url}")

        handles = [
            Submission(MODEL, REQUEST_ID, "http://localhost/status", f"{BASE}/response"),
            Submission(MODEL, REQUEST_ID, f"{BASE}/status", "http://169.254.169.254/latest/meta-data"),
            Submission("fal-ai/unknown", REQUEST_ID, f"{BASE}/status", f"{BASE}/response"),
            Submission(MODEL, "../other", f"{BASE}/status", f"{BASE}/response"),
        ]
        with httpx.Client(transport=httpx.MockTransport(fail_on_request)) as http:
            client = FalQueueClient("test-key", http_client=http)
            for handle in handles:
                with self.subTest(handle=handle), self.assertRaises(ProviderError) as raised:
                    client.status(handle)
                self.assertEqual(raised.exception.code, "unsafe_url")
                with self.subTest(handle=handle), self.assertRaises(ProviderError) as raised:
                    client.result(handle)
                self.assertEqual(raised.exception.code, "unsafe_url")

    def test_result_rejects_non_fal_media_url_for_private_download(self):
        bad_urls = [
            "http://v3b.fal.media/files/output.mp4",
            "https://v3b.fal.media.evil.test/files/output.mp4",
            "https://127.0.0.1/output.mp4",
            "https://v3b.fal.media@127.0.0.1/output.mp4",
        ]
        handle = Submission(MODEL, REQUEST_ID, f"{BASE}/status", f"{BASE}/response")
        for bad_url in bad_urls:
            with self.subTest(url=bad_url):
                def respond(request):
                    return httpx.Response(200, json={"video": {"url": bad_url}})

                with httpx.Client(transport=httpx.MockTransport(respond)) as http:
                    with self.assertRaises(ProviderError) as raised:
                        FalQueueClient("test-key", http_client=http).result(handle)
                self.assertEqual(raised.exception.code, "unsafe_url")

    def test_worker_can_validate_media_url_before_download(self):
        fal.validate_media_url("https://v3b.fal.media/files/b/example/output.mp4")
        with self.assertRaises(ProviderError) as raised:
            fal.validate_media_url("https://127.0.0.1/private.mp4")
        self.assertEqual(raised.exception.code, "unsafe_url")

    def test_http_errors_map_to_stable_worker_codes(self):
        expected = [
            (401, "auth_error", False),
            (402, "billing_error", False),
            (422, "invalid_request", False),
            (429, "rate_limited", True),
            (503, "provider_unavailable", True),
        ]
        handle = Submission(MODEL, REQUEST_ID, f"{BASE}/status", f"{BASE}/response")
        for http_status, code, retryable in expected:
            with self.subTest(http_status=http_status):
                def respond(request):
                    return httpx.Response(http_status, json={"detail": "fal rejected request"})

                with httpx.Client(transport=httpx.MockTransport(respond)) as http:
                    with self.assertRaises(ProviderError) as raised:
                        FalQueueClient("test-key", http_client=http).status(handle)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.http_status, http_status)
                self.assertEqual(raised.exception.retryable, retryable)

    def test_http_timeout_on_submit_is_marked_indeterminate(self):
        def timeout(request):
            self.assertEqual(request.extensions["timeout"]["read"], 2.5)
            raise httpx.ReadTimeout("timed out", request=request)

        with httpx.Client(transport=httpx.MockTransport(timeout)) as http:
            with self.assertRaises(ProviderError) as raised:
                FalQueueClient("test-key", http_client=http, timeout_seconds=2.5).submit(VideoRequest(MODEL, "fox"))

        self.assertEqual(raised.exception.code, "submission_unknown")
        self.assertFalse(raised.exception.retryable)

    def test_timeout_on_status_is_retryable(self):
        handle = Submission(MODEL, REQUEST_ID, f"{BASE}/status", f"{BASE}/response")

        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        with httpx.Client(transport=httpx.MockTransport(timeout)) as http:
            with self.assertRaises(ProviderError) as raised:
                FalQueueClient("test-key", http_client=http).status(handle)

        self.assertEqual(raised.exception.code, "transport_error")
        self.assertTrue(raised.exception.retryable)

    def test_redirect_is_not_followed_with_api_key(self):
        def respond(request):
            return httpx.Response(302, headers={"location": "http://127.0.0.1/steal"})

        with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as http:
            with self.assertRaises(ProviderError) as raised:
                FalQueueClient("test-key", http_client=http).submit(VideoRequest(MODEL, "fox"))

        self.assertEqual(raised.exception.code, "provider_response")

    def test_malformed_provider_payload_raises_typed_error(self):
        handle = Submission(MODEL, REQUEST_ID, f"{BASE}/status", f"{BASE}/response")
        fixtures = [
            ("submit", {"status_url": f"{BASE}/status", "response_url": f"{BASE}/response"}),
            ("status", {"status": "UNKNOWN"}),
            ("result", {"video": {}}),
        ]
        for method, payload in fixtures:
            with self.subTest(method=method):
                def respond(request):
                    return httpx.Response(200, json=payload)

                with httpx.Client(transport=httpx.MockTransport(respond)) as http:
                    client = FalQueueClient("test-key", http_client=http)
                    with self.assertRaises(ProviderError) as raised:
                        if method == "submit":
                            client.submit(VideoRequest(MODEL, "fox"))
                        elif method == "status":
                            client.status(handle)
                        else:
                            client.result(handle)
                self.assertEqual(raised.exception.code, "provider_response")


if __name__ == "__main__":
    unittest.main()
