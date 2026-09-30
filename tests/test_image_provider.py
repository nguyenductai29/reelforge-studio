"""Runway image adapter and image file checks, offline (httpx.MockTransport)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import httpx

from app.image_files import MAX_IMAGE_BYTES, ImageInfo, inspect_image
from app.providers.image import (IMAGE_PROVIDERS, ImageProviderError, ImageRequest, ImageSubmission,
                                 RunwayImageProvider, create_image_provider, image_credit_cost, image_model,
                                 image_provider_config_issue, validate_image_request)

TASK = "3a4c1b2e-5d6f-4a7b-8c9d-0e1f2a3b4c5d"
HOST = "dnznrvs05pmza.cloudfront.net"
OUTPUT = f"https://{HOST}/abc/image.png?_jwt=signed"


def png(width=4, height=3):
    """A minimal complete PNG: signature, IHDR, IEND."""
    ihdr = b"IHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + ihdr + b"\x00\x00\x00\x00"
            + b"\x00\x00\x00\x00IEND\xaeB`\x82")


def jpeg(width=5, height=6):
    sof = b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9"


def webp(width=7, height=8):
    body = b"VP8X" + (10).to_bytes(4, "little") + b"\x00" * 4 + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + (4 + len(body)).to_bytes(4, "little") + b"WEBP" + body


def provider(handler):
    return RunwayImageProvider("runway-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


@patch.dict(os.environ, {"RUNWAY_OUTPUT_HOSTS": HOST, "RUNWAYML_API_SECRET": "runway-key"})
class RunwayImageTest(unittest.TestCase):
    def test_submit_sends_the_documented_request(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"id": TASK})

        submission = provider(handler).submit(ImageRequest("gen4_image", "A red boat", "16:9", "standard", seed=7))
        request = seen[0]
        self.assertEqual((request.method, str(request.url)), ("POST", "https://api.dev.runwayml.com/v1/text_to_image"))
        self.assertEqual(request.headers["Authorization"], "Bearer runway-key")
        self.assertEqual(request.headers["X-Runway-Version"], "2024-11-06")
        self.assertEqual(json.loads(request.content),
                         {"model": "gen4_image", "promptText": "A red boat", "ratio": "1280:720", "seed": 7})
        self.assertEqual(request.extensions["timeout"]["read"], 10.0)
        self.assertEqual(submission, ImageSubmission("runway", "gen4_image", TASK))

    def test_ratios_follow_quality(self):
        ratios = []

        def handler(request):
            ratios.append(json.loads(request.content)["ratio"])
            return httpx.Response(200, json={"id": TASK})

        client = provider(handler)
        for aspect in ("1:1", "16:9", "9:16"):
            for quality in ("standard", "high"):
                client.submit(ImageRequest("gen4_image", "x", aspect, quality))
        self.assertEqual(ratios, ["720:720", "1080:1080", "1280:720", "1920:1080", "720:1280", "1080:1920"])

    def test_requests_are_checked_before_any_call(self):
        def handler(request):
            self.fail("no request expected")

        client = provider(handler)
        cases = [(ImageRequest("gen5_image", "x"), "unsupported_model"),
                 (ImageRequest("gen4_image", "  "), "invalid_request"),
                 (ImageRequest("gen4_image", "x" * 1001), "invalid_request"),
                 (ImageRequest("gen4_image", "x", "4:3"), "invalid_request"),
                 (ImageRequest("gen4_image", "x", quality="ultra"), "invalid_request"),
                 (ImageRequest("gen4_image", "x", seed=-1), "invalid_request"),
                 (ImageRequest("gen4_image", "x", negative_prompt="blur"), "invalid_request"),
                 (ImageRequest("gen4_image", "x", reference_image_url="https://x/y.png"), "invalid_request")]
        for request, code in cases:
            with self.subTest(request=request), self.assertRaises(ImageProviderError) as caught:
                client.submit(request)
            self.assertEqual(caught.exception.code, code)

    def test_status_and_result(self):
        states = iter([{"id": TASK, "status": "PENDING"}, {"id": TASK, "status": "RUNNING"},
                       {"id": TASK, "status": "SUCCEEDED", "output": [OUTPUT]},
                       {"id": TASK, "status": "SUCCEEDED", "output": [OUTPUT]}])

        def handler(request):
            self.assertEqual(str(request.url), f"https://api.dev.runwayml.com/v1/tasks/{TASK}")
            return httpx.Response(200, json=next(states))

        client, submission = provider(handler), ImageSubmission("runway", "gen4_image", TASK)
        self.assertEqual([client.status(submission).state for _ in range(3)], ["queued", "running", "completed"])
        result = client.result(submission)
        self.assertEqual((result.remote_request_id, [image.url for image in result.images]), (TASK, [OUTPUT]))

    def test_failures_and_unsafe_outputs(self):
        def failed(request):
            return httpx.Response(200, json={"id": TASK, "status": "FAILED", "failureCode": "SAFETY.INPUT.TEXT",
                                             "failure": "Prompt was flagged"})

        state = provider(failed).status(ImageSubmission("runway", "gen4_image", TASK))
        self.assertEqual((state.state, state.error.code, state.error.retryable), ("failed", "SAFETY.INPUT.TEXT", False))
        for url in ("http://%s/a.png" % HOST, "https://evil.example/a.png", f"https://{HOST}/a.svg",
                    f"https://{HOST}/a.png#x", f"https://user@{HOST}/a.png"):
            def returns(request, url=url):
                return httpx.Response(200, json={"id": TASK, "status": "SUCCEEDED", "output": [url]})

            with self.subTest(url=url), self.assertRaises(ImageProviderError) as caught:
                provider(returns).result(ImageSubmission("runway", "gen4_image", TASK))
            self.assertEqual(caught.exception.code, "unsafe_url")
        with self.assertRaises(ImageProviderError):
            provider(failed).status(ImageSubmission("runway", "gen4_image", "not-a-uuid"))

    def test_http_errors_keep_their_categories(self):
        for status, code, category in ((401, "authentication_error", "authentication_error"),
                                       (429, "rate_limited", "rate_limited"),
                                       (400, "invalid_request", "invalid_request"),
                                       (503, "submission_unknown", "provider_unavailable")):
            with self.subTest(status=status), self.assertRaises(ImageProviderError) as caught:
                provider(lambda request, s=status: httpx.Response(s, json={"error": "nope"})).submit(
                    ImageRequest("gen4_image", "x"))
            self.assertEqual((caught.exception.code, caught.exception.category), (code, category))
            self.assertNotIn("runway-key", str(caught.exception))

        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        with self.assertRaises(ImageProviderError) as caught:
            provider(slow).submit(ImageRequest("gen4_image", "x"))
        self.assertEqual((caught.exception.code, caught.exception.category), ("submission_unknown", "timeout"))

    def test_generate_polls_until_done(self):
        replies = iter([{"id": TASK}, {"id": TASK, "status": "RUNNING"}, {"id": TASK, "status": "SUCCEEDED"},
                        {"id": TASK, "status": "SUCCEEDED", "output": [OUTPUT]}])
        result = provider(lambda request: httpx.Response(200, json=next(replies))).generate(
            ImageRequest("gen4_image", "x"), sleep=lambda seconds: None)
        self.assertEqual(result.images[0].url, OUTPUT)

    def test_catalog_and_configuration(self):
        self.assertEqual(list(IMAGE_PROVIDERS), ["runway"])
        self.assertEqual(image_model("runway", "gen4_image").aspect_ratios, ("1:1", "16:9", "9:16"))
        self.assertIsNone(image_model("runway", "gen4_image_turbo"))
        self.assertIsNone(image_provider_config_issue("runway"))
        with patch.dict(os.environ, {"RUNWAYML_API_SECRET": ""}):
            self.assertEqual(image_provider_config_issue("runway")[0], "missing_key")
            with self.assertRaises(ImageProviderError):
                create_image_provider("runway")
        with patch.dict(os.environ, {"RUNWAY_OUTPUT_HOSTS": ""}):
            self.assertEqual(image_provider_config_issue("runway")[0], "invalid_config")
        self.assertEqual(image_provider_config_issue("dalle")[0], "unsupported_provider")
        self.assertEqual(image_credit_cost(), 2)
        with patch.dict(os.environ, {"IMAGE_CREDITS_PER_GENERATION": "5"}):
            self.assertEqual(image_credit_cost(), 5)
        validate_image_request(image_model("runway", "gen4_image"), ImageRequest("gen4_image", "x", seed=4294967295))


class ImageFileTest(unittest.TestCase):
    def check(self, data, name="image"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(data)
            return inspect_image(path)

    def test_png_jpeg_and_webp_are_recognized_by_their_bytes(self):
        self.assertEqual(self.check(png(640, 360)), ImageInfo("image/png", "png", 640, 360))
        self.assertEqual(self.check(jpeg(320, 240)), ImageInfo("image/jpeg", "jpg", 320, 240))
        self.assertEqual(self.check(webp(100, 50)), ImageInfo("image/webp", "webp", 100, 50))

    def test_other_and_broken_files_are_rejected(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        cases = {"svg": svg, "html": b"<html><body>error</body></html>", "gif": b"GIF89a" + b"\x00" * 40,
                 "truncated png": png()[:-12], "truncated jpeg": jpeg()[:-2], "truncated webp": webp()[:-4],
                 "empty": b"", "zero size": png(0, 3)}
        for name, data in cases.items():
            with self.subTest(case=name), self.assertRaises((ValueError, OSError)):
                self.check(data)

    def test_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big"
            path.write_bytes(png() + b"\x00" * 64)
            with self.assertRaises(ValueError):
                inspect_image(path, max_bytes=32)
        self.assertEqual(MAX_IMAGE_BYTES, 20 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
