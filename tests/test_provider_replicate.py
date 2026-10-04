"""Replicate prediction adapter contracts without live API calls."""

import json
import unittest

import httpx

try:
    from app.providers.replicate import (
        ProviderError,
        ReplicateClient,
        Submission,
        VideoRequest,
        validate_media_url,
    )
except ImportError:
    ReplicateClient = None


MODEL = "google/veo-3.1-fast"
PREDICTION_ID = "7deqgxa70hrmc0csx1ksx53g3g"
PREDICTION_URL = f"https://api.replicate.com/v1/predictions/{PREDICTION_ID}"
VIDEO_URL = "https://replicate.delivery/xezq/1B4tYHYfzWzDA6QU9CEDxkCnStf8YeysV0TlfnKktgG3la9VB/tmpyxaslbuk.mp4"


def prediction(status="starting", *, output=None, error=None):
    return {"id": PREDICTION_ID, "model": MODEL, "status": status,
            "output": output, "error": error, "urls": {"get": PREDICTION_URL}}


def submission():
    return Submission(model_id=MODEL, request_id=PREDICTION_ID,
                      status_url=PREDICTION_URL, response_url=PREDICTION_URL)


class ReplicateClientTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ReplicateClient, "Replicate adapter is missing")

    def test_submit_posts_official_model_input_async_and_returns_validated_handle(self):
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(201, json=prediction())

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            result = ReplicateClient("secret", http_client=http_client).submit(VideoRequest(
                model_id=MODEL, prompt="A lighthouse at sunrise", aspect_ratio="9:16",
                duration="6s", resolution="720p", generate_audio=False,
            ))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].method, "POST")
        self.assertEqual(str(sent[0].url), "https://api.replicate.com/v1/models/google/veo-3.1-fast/predictions")
        self.assertEqual(sent[0].headers["authorization"], "Bearer secret")
        self.assertEqual(json.loads(sent[0].content), {"input": {
            "prompt": "A lighthouse at sunrise", "aspect_ratio": "9:16", "duration": 6,
            "resolution": "720p", "generate_audio": False,
        }})
        self.assertEqual(result, submission())

    def test_unsupported_options_reject_before_http(self):
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(201, json=prediction())

        invalid = (
            VideoRequest(model_id="other/model", prompt="video"),
            VideoRequest(model_id=MODEL, prompt=" "),
            VideoRequest(model_id=MODEL, prompt="video", aspect_ratio="1:1"),
            VideoRequest(model_id=MODEL, prompt="video", duration="5s"),
            VideoRequest(model_id=MODEL, prompt="video", resolution="4k"),
            VideoRequest(model_id=MODEL, prompt="video", generate_audio="yes"),
        )
        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            client = ReplicateClient("secret", http_client=http_client)
            for request in invalid:
                with self.subTest(request=request), self.assertRaises(ProviderError):
                    client.submit(request)
        self.assertEqual(sent, [])

    def test_poll_maps_all_prediction_states_and_result_url(self):
        answers = iter([
            prediction("starting"), prediction("processing"), prediction("succeeded", output=VIDEO_URL),
            prediction("succeeded", output=VIDEO_URL),
        ])
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(200, json=next(answers))

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            client = ReplicateClient("secret", http_client=http_client)
            self.assertEqual(client.status(submission()).state, "queued")
            self.assertEqual(client.status(submission()).state, "running")
            self.assertEqual(client.status(submission()).state, "completed")
            self.assertEqual(client.result(submission()).video_url, VIDEO_URL)
        self.assertEqual(len(sent), 4)
        for request in sent:
            self.assertEqual(request.method, "GET")
            self.assertEqual(str(request.url), PREDICTION_URL)

    def test_failed_canceled_and_aborted_predictions_are_terminal(self):
        for raw_state in ("failed", "canceled", "aborted"):
            with self.subTest(raw_state=raw_state):
                with httpx.Client(transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json=prediction(raw_state, error="Generation stopped")),
                )) as http_client:
                    status = ReplicateClient("secret", http_client=http_client).status(submission())
                self.assertEqual(status.state, "failed")
                self.assertFalse(status.error.retryable)

    def test_submission_rejects_untrusted_prediction_url(self):
        for bad_url in ("http://api.replicate.com/v1/predictions/" + PREDICTION_ID,
                        "https://api.replicate.com.evil.test/v1/predictions/" + PREDICTION_ID,
                        "https://api.replicate.com@evil.test/v1/predictions/" + PREDICTION_ID,
                        "https://api.replicate.com/v1/predictions/other"):
            def handle(_request):
                data = prediction()
                data["urls"]["get"] = bad_url
                return httpx.Response(201, json=data)
            with self.subTest(bad_url=bad_url):
                with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
                    with self.assertRaises(ProviderError):
                        ReplicateClient("secret", http_client=http_client).submit(VideoRequest(model_id=MODEL, prompt="video"))

    def test_persisted_prediction_url_is_revalidated_before_fetch(self):
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(200, json=prediction("processing"))

        forged = Submission(model_id=MODEL, request_id=PREDICTION_ID,
                            status_url="https://evil.test/steal", response_url=PREDICTION_URL)
        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            with self.assertRaises(ProviderError):
                ReplicateClient("secret", http_client=http_client).status(forged)
        self.assertEqual(sent, [])

    def test_poll_rejects_different_prediction_id(self):
        def handle(_request):
            data = prediction("succeeded", output=VIDEO_URL)
            data["id"] = "wrongpredictionidentifier1"
            return httpx.Response(200, json=data)

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            with self.assertRaises(ProviderError) as raised:
                ReplicateClient("secret", http_client=http_client).status(submission())
        self.assertEqual(raised.exception.code, "invalid_response")

    def test_result_accepts_only_replicate_delivery_https_mp4(self):
        for bad_url in (
            "http://replicate.delivery/video.mp4",
            "https://replicate.delivery.evil.test/video.mp4",
            "https://replicate.delivery@evil.test/video.mp4",
            "https://replicate.delivery:443/video.mp4",
            "https://replicate.delivery/image.png",
            "https://replicate.delivery/video.mp4?target=127.0.0.1",
        ):
            with self.subTest(bad_url=bad_url), self.assertRaises(ProviderError):
                validate_media_url(bad_url)
        validate_media_url(VIDEO_URL)
        validate_media_url("https://subdomain.replicate.delivery/folder/video.mp4")

    def test_result_rejects_provider_media_url_outside_allowlist(self):
        def handle(_request):
            return httpx.Response(200, json=prediction("succeeded", output="https://evil.test/video.mp4"))

        with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
            with self.assertRaises(ProviderError) as raised:
                ReplicateClient("secret", http_client=http_client).result(submission())
        self.assertEqual(raised.exception.code, "unsafe_url")

    def test_transport_and_http_errors_have_stable_retry_decisions(self):
        for status, expected in ((400, ("invalid_request", False)), (401, ("authentication_error", False)),
                                 (402, ("billing_error", False)), (429, ("rate_limited", True)),
                                 (503, ("provider_unavailable", True))):
            with self.subTest(status=status):
                with httpx.Client(transport=httpx.MockTransport(
                    lambda _request: httpx.Response(status, json={"detail": "failed"}),
                )) as http_client:
                    with self.assertRaises(ProviderError) as raised:
                        ReplicateClient("secret", http_client=http_client).status(submission())
                self.assertEqual((raised.exception.code, raised.exception.retryable), expected)

        def timeout(request):
            raise httpx.ReadTimeout("timeout", request=request)

        with httpx.Client(transport=httpx.MockTransport(timeout)) as http_client:
            client = ReplicateClient("secret", http_client=http_client)
            with self.assertRaises(ProviderError) as raised:
                client.status(submission())
            self.assertEqual((raised.exception.code, raised.exception.retryable), ("timeout", True))
            with self.assertRaises(ProviderError) as raised:
                client.submit(VideoRequest(model_id=MODEL, prompt="video"))
            self.assertEqual((raised.exception.code, raised.exception.retryable), ("submission_unknown", False))


if __name__ == "__main__":
    unittest.main()
