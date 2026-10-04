"""Runware REST video adapter contracts, exercised without external requests."""

import json
import unittest
import uuid

import httpx

try:
    from app.providers.runware import (
        JobStatus,
        ProviderError,
        RunwareClient,
        Submission,
        VideoRequest,
        validate_media_url,
    )
except ImportError:
    RunwareClient = None


TASK_ID = "24cd5dff-cb81-4db5-8506-b72a9425f9d1"
VIDEO_URL = "https://vm.runware.ai/video/os/a14d18/ws/2/vi/b7db282d-2943-4f12-992f-77df3ad3ec71.mp4"
MODEL = "bytedance:seedance@2.5"


def response(payload, status_code=200):
    return httpx.Response(status_code, json=payload)


def submission():
    return Submission(model_id=MODEL, request_id=TASK_ID)


class RunwareClientTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(RunwareClient, "Runware adapter is missing")

    def test_submit_posts_allowed_model_with_async_json_array_and_bearer_auth(self):
        sent = []

        def handle(request):
            sent.append(request)
            task = json.loads(request.content)[0]
            return response({"data": [{"taskType": "videoInference", "taskUUID": task["taskUUID"], "status": "processing"}]})

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            result = RunwareClient("secret", http_client=http_client).submit(VideoRequest(
                model_id=MODEL, prompt="A quiet harbor at dawn", aspect_ratio="9:16",
                duration="8s", resolution="720p", generate_audio=False,
            ))

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].method, "POST")
        self.assertEqual(str(sent[0].url), "https://api.runware.ai/v1")
        self.assertEqual(sent[0].headers["authorization"], "Bearer secret")
        self.assertEqual(sent[0].headers["content-type"], "application/json")
        self.assertEqual(json.loads(sent[0].content), [{
            "taskType": "videoInference", "taskUUID": result.request_id,
            "model": MODEL, "positivePrompt": "A quiet harbor at dawn",
            "width": 720, "height": 1280, "duration": 8,
            "settings": {"audio": False}, "deliveryMethod": "async", "outputFormat": "MP4",
        }])
        self.assertEqual(uuid.UUID(result.request_id).version, 4)
        self.assertEqual(result.model_id, MODEL)

    def test_invalid_capabilities_and_unknown_model_reject_before_http(self):
        sent = []

        def handle(request):
            sent.append(request)
            return response({"data": []})

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            client = RunwareClient("secret", http_client=http_client)
            cases = (
                VideoRequest(model_id="other:unlisted@1", prompt="video"),
                VideoRequest(model_id=MODEL, prompt="   "),
                VideoRequest(model_id=MODEL, prompt="video", aspect_ratio="1:1"),
                VideoRequest(model_id=MODEL, prompt="video", duration="31s"),
                VideoRequest(model_id=MODEL, prompt="video", duration="8.5s"),
                VideoRequest(model_id=MODEL, prompt="video", resolution="4k"),
                VideoRequest(model_id=MODEL, prompt="video", generate_audio="yes"),
            )
            for case in cases:
                with self.subTest(case=case), self.assertRaises(ProviderError):
                    client.submit(case)
        self.assertEqual(sent, [])

    def test_poll_maps_processing_then_success_and_returns_https_mp4(self):
        replies = iter([
            {"data": [{"taskType": "videoInference", "taskUUID": TASK_ID, "status": "processing", "progress": 47}]},
            {"data": [{"taskType": "videoInference", "taskUUID": TASK_ID, "status": "success", "videoURL": VIDEO_URL}]},
            {"data": [{"taskType": "videoInference", "taskUUID": TASK_ID, "status": "success", "videoURL": VIDEO_URL}]},
        ])
        sent = []

        def handle(request):
            sent.append(request)
            return response(next(replies))

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            client = RunwareClient("secret", http_client=http_client)
            self.assertEqual(client.status(submission()).state, "running")
            self.assertEqual(client.status(submission()).state, "completed")
            self.assertEqual(client.result(submission()).video_url, VIDEO_URL)

        self.assertEqual(len(sent), 3)
        for request in sent:
            self.assertEqual(json.loads(request.content), [{"taskType": "getResponse", "taskUUID": TASK_ID}])

    def test_poll_returns_failed_job_for_provider_error(self):
        def handle(_request):
            return response({"errors": [{"taskUUID": TASK_ID, "status": "error", "code": "timeoutProvider", "message": "Timed out"}]})

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            state = RunwareClient("secret", http_client=http_client).status(submission())
        self.assertEqual(state.state, "failed")
        self.assertEqual(state.error.code, "timeoutProvider")
        self.assertTrue(state.error.retryable)

    def test_submit_rejects_mismatched_acknowledgment(self):
        def handle(_request):
            return response({"data": [{"taskType": "videoInference", "taskUUID": TASK_ID, "status": "processing"}]})

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            with self.assertRaises(ProviderError) as raised:
                RunwareClient("secret", http_client=http_client).submit(VideoRequest(model_id=MODEL, prompt="hello"))
        self.assertEqual(raised.exception.code, "invalid_response")

    def test_poll_rejects_unrelated_or_malformed_response(self):
        def handle(_request):
            return response({"data": [{"taskType": "videoInference", "taskUUID": "other", "status": "success", "videoURL": VIDEO_URL}]})

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            with self.assertRaises(ProviderError) as raised:
                RunwareClient("secret", http_client=http_client).status(submission())
        self.assertEqual(raised.exception.code, "invalid_response")

    def test_result_rejects_media_url_outside_runware_video_host(self):
        for bad_url in (
            "http://vm.runware.ai/video/file.mp4",
            "https://vm.runware.ai.evil.test/video/file.mp4",
            "https://vm.runware.ai@evil.test/video/file.mp4",
            "https://vm.runware.ai:443/video/file.mp4",
            "https://vm.runware.ai/other/file.mp4",
            "https://vm.runware.ai/video/file.mp4?next=127.0.0.1",
        ):
            with self.subTest(bad_url=bad_url), self.assertRaises(ProviderError):
                validate_media_url(bad_url)
        validate_media_url(VIDEO_URL)

    def test_result_rejects_unsafe_video_url_from_provider(self):
        def handle(_request):
            return response({"data": [{"taskType": "videoInference", "taskUUID": TASK_ID,
                                       "status": "success", "videoURL": "https://evil.test/video.mp4"}]})

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            with self.assertRaises(ProviderError) as raised:
                RunwareClient("secret", http_client=http_client).result(submission())
        self.assertEqual(raised.exception.code, "unsafe_url")

    def test_http_and_transport_errors_have_stable_retry_decisions(self):
        for status, code, retryable in ((400, "invalid_request", False), (401, "authentication_error", False),
                                        (402, "billing_error", False), (429, "rate_limited", True),
                                        (503, "provider_unavailable", True)):
            with self.subTest(status=status):
                with httpx.Client(transport=httpx.MockTransport(lambda _request: response({"errors": []}, status))) as http_client:
                    with self.assertRaises(ProviderError) as raised:
                        RunwareClient("secret", http_client=http_client).status(submission())
                self.assertEqual((raised.exception.code, raised.exception.retryable), (code, retryable))
        def timeout(request):
            raise httpx.ReadTimeout("timeout", request=request)
        with httpx.Client(transport=httpx.MockTransport(timeout)) as http_client:
            client = RunwareClient("secret", http_client=http_client)
            with self.assertRaises(ProviderError) as raised:
                client.status(submission())
            self.assertEqual((raised.exception.code, raised.exception.retryable), ("timeout", True))
            with self.assertRaises(ProviderError) as raised:
                client.submit(VideoRequest(model_id=MODEL, prompt="hello"))
            self.assertEqual((raised.exception.code, raised.exception.retryable), ("submission_unknown", False))


if __name__ == "__main__":
    unittest.main()
