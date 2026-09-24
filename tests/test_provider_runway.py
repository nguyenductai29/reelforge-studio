"""Runway Dev Gen-4.5 text-to-video contract without external requests."""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.providers.runway import (
    ProviderError,
    RunwayClient,
    Submission,
    VideoRequest,
    validate_media_url,
    validate_output_hosts,
)


MODEL = "gen4.5"
TASK_ID = "d2e3d1f4-1b3c-4b5c-8d46-1c1d7ee86892"
TASK_URL = f"https://api.dev.runwayml.com/v1/tasks/{TASK_ID}"
VIDEO_URL = "https://dnznrvs05pmza.cloudfront.net/output.mp4?_jwt=signed-token"


def submission():
    return Submission(model_id=MODEL, request_id=TASK_ID, status_url=TASK_URL, response_url=TASK_URL)


def task(state="PENDING", **extra):
    return {"id": TASK_ID, "status": state, **extra}


class RunwayClientTests(unittest.TestCase):
    def test_submit_uses_text_to_video_with_required_version_and_no_unsupported_fields(self):
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(200, json={"id": TASK_ID, "estimatedCost": {"credits": 96}})

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            result = RunwayClient("secret", http_client=http_client).submit(VideoRequest(
                model_id=MODEL, prompt="A fox running at sunrise", aspect_ratio="9:16",
                duration="8s", resolution="720p", generate_audio=False,
            ))
        self.assertEqual(result, submission())
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].method, "POST")
        self.assertEqual(str(sent[0].url), "https://api.dev.runwayml.com/v1/text_to_video")
        self.assertEqual(sent[0].headers["authorization"], "Bearer secret")
        self.assertEqual(sent[0].headers["x-runway-version"], "2024-11-06")
        self.assertEqual(json.loads(sent[0].content), {
            "model": MODEL, "promptText": "A fox running at sunrise", "ratio": "720:1280", "duration": 8,
        })

    def test_rejects_unsupported_request_without_network(self):
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(200, json={"id": TASK_ID})

        invalid = (
            VideoRequest(model_id="gen4_turbo", prompt="fox", generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="  ", generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="😀" * 501, generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="bad\ud800", generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="fox", aspect_ratio="1:1", generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="fox", duration="11s", generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="fox", duration="2.5s", generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="fox", duration="9" * 5000 + "s", generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="fox", resolution="1080p", generate_audio=False),
            VideoRequest(model_id=MODEL, prompt="fox", generate_audio=True),
            VideoRequest(model_id=MODEL, prompt="fox", generate_audio="false"),
        )
        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            client = RunwayClient("secret", http_client=http_client)
            for request in invalid:
                with self.subTest(request=request), self.assertRaises(ProviderError):
                    client.submit(request)
        self.assertEqual(sent, [])

    def test_status_maps_documented_task_states_and_failure(self):
        states = iter([
            task("PENDING"), task("THROTTLED"), task("RUNNING"),
            task("SUCCEEDED", output=[VIDEO_URL]),
            task("FAILED", failure="The prompt was rejected", failureCode="SAFETY.INPUT.TEXT"),
            task("CANCELLED"),
        ])
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(200, json=next(states))

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            client = RunwayClient("secret", http_client=http_client)
            self.assertEqual([client.status(submission()).state for _ in range(4)],
                             ["queued", "queued", "running", "completed"])
            failed = client.status(submission())
            cancelled = client.status(submission())
        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.error.code, "SAFETY.INPUT.TEXT")
        self.assertFalse(failed.error.retryable)
        self.assertEqual(cancelled.error.code, "provider_canceled")
        self.assertTrue(all(request.method == "GET" and str(request.url) == TASK_URL for request in sent))
        self.assertTrue(all(request.headers["x-runway-version"] == "2024-11-06" for request in sent))

    def test_result_requires_succeeded_single_allowlisted_mp4(self):
        with patch.dict(os.environ, {"RUNWAY_OUTPUT_HOSTS": "dnznrvs05pmza.cloudfront.net"}):
            with httpx.Client(transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json=task("SUCCEEDED", output=[VIDEO_URL])),
            )) as http_client:
                result = RunwayClient("secret", http_client=http_client).result(submission())
        self.assertEqual(result.video_url, VIDEO_URL)
        self.assertEqual(result.content_type, "video/mp4")

        for response in (task("RUNNING"), task("SUCCEEDED", output=[]),
                         task("SUCCEEDED", output=[VIDEO_URL, VIDEO_URL]),
                         task("SUCCEEDED", output=["https://127.0.0.1/output.mp4"])):
            with self.subTest(response=response), patch.dict(os.environ, {"RUNWAY_OUTPUT_HOSTS": "dnznrvs05pmza.cloudfront.net"}):
                with httpx.Client(transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json=response),
                )) as http_client:
                    with self.assertRaises(ProviderError):
                        RunwayClient("secret", http_client=http_client).result(submission())

    def test_media_url_requires_explicit_exact_host_allowlist(self):
        with patch.dict(os.environ, {"RUNWAY_OUTPUT_HOSTS": ""}):
            with self.assertRaises(ProviderError) as raised:
                validate_media_url(VIDEO_URL)
            self.assertEqual(raised.exception.code, "invalid_config")
        with patch.dict(os.environ, {"RUNWAY_OUTPUT_HOSTS": "dnznrvs05pmza.cloudfront.net"}):
            validate_media_url(VIDEO_URL)
            for bad_url in (
                "http://dnznrvs05pmza.cloudfront.net/output.mp4",
                "https://dnznrvs05pmza.cloudfront.net.evil.test/output.mp4",
                "https://dnznrvs05pmza.cloudfront.net@127.0.0.1/output.mp4",
                "https://dnznrvs05pmza.cloudfront.net:443/output.mp4",
                "https://dnznrvs05pmza.cloudfront.net/output.png",
                "https://dnznrvs05pmza.cloudfront.net/output.mp4#fragment",
            ):
                with self.subTest(bad_url=bad_url), self.assertRaises(ProviderError):
                    validate_media_url(bad_url)
        for bad_config in ("*.cloudfront.net", "localhost", "https://example.com", "example.com:443", ","):
            with self.subTest(bad_config=bad_config), self.assertRaises(ProviderError):
                validate_output_hosts(bad_config)
        self.assertEqual(validate_output_hosts(" dnznrvs05pmza.cloudfront.net, CDN.EXAMPLE.COM "),
                         frozenset({"dnznrvs05pmza.cloudfront.net", "cdn.example.com"}))

    def test_submission_and_task_identity_are_fenced_before_or_after_network(self):
        forged = Submission(MODEL, TASK_ID, "https://evil.test/steal", TASK_URL)
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(200, json=task())

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            with self.assertRaises(ProviderError):
                RunwayClient("secret", http_client=http_client).status(forged)
        self.assertEqual(sent, [])

        with httpx.Client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"id": "3e144628-b9b7-4ae8-98f6-d882c2dde5d8"}),
        )) as http_client:
            with self.assertRaises(ProviderError):
                RunwayClient("secret", http_client=http_client).status(submission())

    def test_http_and_transport_errors_never_retry_ambiguous_submit(self):
        for status, expected in ((400, ("invalid_request", False)), (401, ("auth_error", False)),
                                 (402, ("billing_error", False)), (429, ("rate_limited", True)),
                                 (503, ("provider_unavailable", True))):
            with self.subTest(status=status):
                with httpx.Client(transport=httpx.MockTransport(
                    lambda _request: httpx.Response(status, json={"error": "failed"}),
                )) as http_client:
                    with self.assertRaises(ProviderError) as raised:
                        RunwayClient("secret", http_client=http_client).status(submission())
                self.assertEqual((raised.exception.code, raised.exception.retryable), expected)

        with httpx.Client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, json={"error": "failed"}),
        )) as http_client:
            with self.assertRaises(ProviderError) as raised:
                RunwayClient("secret", http_client=http_client).submit(VideoRequest(MODEL, "fox", generate_audio=False))
        self.assertEqual((raised.exception.code, raised.exception.retryable), ("submission_unknown", False))

        def timeout(request):
            raise httpx.ReadTimeout("timeout", request=request)

        with httpx.Client(transport=httpx.MockTransport(timeout)) as http_client:
            client = RunwayClient("secret", http_client=http_client)
            with self.assertRaises(ProviderError) as raised:
                client.status(submission())
            self.assertEqual((raised.exception.code, raised.exception.retryable), ("transport_error", True))
            with self.assertRaises(ProviderError) as raised:
                client.submit(VideoRequest(MODEL, "fox", generate_audio=False))
            self.assertEqual((raised.exception.code, raised.exception.retryable), ("submission_unknown", False))


if __name__ == "__main__":
    unittest.main()
