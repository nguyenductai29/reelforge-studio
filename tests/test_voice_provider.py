"""Gemini text-to-speech adapter and audio file checks, offline (httpx.MockTransport)."""
import base64
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.audio_files import AudioInfo, inspect_audio, pcm_to_wav
from app.providers.voice import (ALL_VOICES, VOICE_PROVIDERS, GeminiVoiceProvider, VoiceProviderError, VoiceRequest,
                                 create_voice_provider, voice_credit_cost, voice_model, voice_provider_config_issue)

MODEL = "gemini-2.5-flash-preview-tts"
PCM = b"\x01\x00\xff\xff" * 12000  # 24,000 samples: one second at 24 kHz


def reply(pcm=PCM, mime="audio/L16;codec=pcm;rate=24000", **extra):
    return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime,
                                                                  "data": base64.b64encode(pcm).decode()}}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 25, "totalTokenCount": 34},
            "responseId": "resp-123", "modelVersion": MODEL, **extra}


def provider(handler):
    return GeminiVoiceProvider("gemini-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def mp3_frame():
    # MPEG-1 Layer III, 128 kbit/s, 44.1 kHz, no padding: 417 bytes per frame.
    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    return header + b"\x00" * (144 * 128000 // 44100 - 4)


class GeminiVoiceTest(unittest.TestCase):
    def test_request_shape_and_wav_result(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=reply())

        result = provider(handler).generate(VoiceRequest(MODEL, "  Xin chào  ", "Puck", "calm"))
        request = seen[0]
        self.assertEqual(str(request.url),
                         f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent")
        self.assertEqual(request.headers["x-goog-api-key"], "gemini-key")
        self.assertNotIn("key=", str(request.url))
        body = json.loads(request.content)
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "Say calmly: Xin chào")
        self.assertEqual(body["generationConfig"], {"responseModalities": ["AUDIO"], "speechConfig": {
            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}}})
        self.assertEqual((result.provider, result.model, result.content_type, result.duration, result.remote_request_id),
                         ("gemini", MODEL, "audio/wav", 1.0, "resp-123"))
        self.assertEqual(result.usage["totalTokenCount"], 34)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice"
            path.write_bytes(result.audio)
            self.assertEqual(inspect_audio(path), AudioInfo("audio/wav", "wav", 1.0, 24000, 1))

    def test_neutral_style_sends_the_text_alone(self):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=reply())

        provider(handler).generate(VoiceRequest(MODEL, "Một câu.", "Kore"))
        self.assertEqual(bodies[0]["contents"][0]["parts"][0]["text"], "Một câu.")

    def test_requests_are_checked_before_any_call(self):
        def handler(request):
            self.fail("no request expected")

        client = provider(handler)
        cases = [(VoiceRequest("gemini-9-tts", "x", "Kore"), "unsupported_model"),
                 (VoiceRequest(MODEL, "   ", "Kore"), "invalid_request"),
                 (VoiceRequest(MODEL, "x" * 5001, "Kore"), "invalid_request"),
                 (VoiceRequest(MODEL, "x", "Alloy"), "invalid_request"),
                 (VoiceRequest(MODEL, "x", "Kore", style="whisper"), "invalid_request"),
                 (VoiceRequest(MODEL, "x", "Kore", format="mp3"), "invalid_request")]
        for request, code in cases:
            with self.subTest(request=request), self.assertRaises(VoiceProviderError) as caught:
                client.generate(request)
            self.assertEqual(caught.exception.code, code)

    def test_errors_keep_their_categories_and_never_the_key(self):
        for status, code, category in ((400, "invalid_request", "invalid_request"),
                                       (401, "authentication_error", "authentication_error"),
                                       (429, "rate_limited", "rate_limited"),
                                       (503, "provider_unavailable", "provider_unavailable")):
            with self.subTest(status=status), self.assertRaises(VoiceProviderError) as caught:
                provider(lambda request, s=status: httpx.Response(s, json={"error": {"message": "nope"}})).generate(
                    VoiceRequest(MODEL, "x", "Kore"))
            self.assertEqual((caught.exception.code, caught.exception.category), (code, category))
            self.assertNotIn("gemini-key", str(caught.exception))

        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        with self.assertRaises(VoiceProviderError) as caught:
            provider(slow).generate(VoiceRequest(MODEL, "x", "Kore"))
        self.assertEqual((caught.exception.code, caught.exception.category), ("submission_unknown", "timeout"))

    def test_answers_without_valid_audio(self):
        cases = [({"promptFeedback": {"blockReason": "SAFETY"}}, "content_rejected"),
                 ({"candidates": [{"finishReason": "SAFETY"}]}, "content_rejected"),
                 ({"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}, "empty_output"),
                 ({"candidates": []}, "empty_output"),
                 ({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;rate=24000",
                                                                         "data": "not base64!"}}]}}]}, "invalid_response"),
                 (reply(pcm=b"\x01"), "invalid_response"),
                 (reply(mime="text/html"), "invalid_response")]
        for body, code in cases:
            with self.subTest(code=code, body=str(body)[:60]), self.assertRaises(VoiceProviderError) as caught:
                provider(lambda request, b=body: httpx.Response(200, json=b)).generate(VoiceRequest(MODEL, "x", "Kore"))
            self.assertEqual(caught.exception.code, code)

    def test_catalog_configuration_and_price(self):
        self.assertEqual(list(VOICE_PROVIDERS), ["gemini"])
        self.assertIn("Kore", ALL_VOICES)
        self.assertEqual(len(ALL_VOICES), 30)
        self.assertIsNotNone(voice_model("gemini", MODEL))
        self.assertIsNone(voice_model("gemini", "gemini-2.5-flash"))
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertEqual(voice_provider_config_issue("gemini")[0], "missing_key")
            with self.assertRaises(VoiceProviderError):
                create_voice_provider("gemini")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "key"}):
            self.assertIsNone(voice_provider_config_issue("gemini"))
        self.assertEqual(voice_provider_config_issue("elevenlabs")[0], "unsupported_provider")
        with patch.dict(os.environ, {"VOICE_CREDITS_PER_GENERATION": ""}):
            os.environ.pop("VOICE_CREDITS_PER_GENERATION")
            self.assertEqual(voice_credit_cost(), 1)
        with patch.dict(os.environ, {"VOICE_CREDITS_PER_GENERATION": "3"}):
            self.assertEqual(voice_credit_cost(), 3)
        with patch.dict(os.environ, {"VOICE_CREDITS_PER_GENERATION": "0"}), self.assertRaises(RuntimeError):
            voice_credit_cost()


class AudioFileTest(unittest.TestCase):
    def check(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio"
            path.write_bytes(data)
            return inspect_audio(path)

    def test_wav_and_mp3_are_recognized_by_their_bytes(self):
        self.assertEqual(self.check(pcm_to_wav(b"\x00\x00" * 48000, sample_rate=48000)),
                         AudioInfo("audio/wav", "wav", 1.0, 48000, 1))
        stereo = pcm_to_wav(b"\x00\x00" * 44100 * 2 * 3, sample_rate=44100, channels=2)
        self.assertEqual(self.check(stereo).duration, 3.0)
        self.assertEqual(self.check(mp3_frame() * 3), AudioInfo("audio/mpeg", "mp3", None))
        tag = b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10
        self.assertEqual(self.check(tag + mp3_frame() * 2).content_type, "audio/mpeg")

    def test_other_and_broken_files_are_rejected(self):
        wav = pcm_to_wav(b"\x00\x00" * 100, sample_rate=24000)
        float_wav = wav[:20] + struct.pack("<H", 3) + wav[22:]
        cases = {"html": b"<html><body>Error</body></html>", "json": b'{"error": "quota"}', "empty": b"",
                 "truncated wav": wav[:-10], "float wav": float_wav, "ogg": b"OggS" + b"\x00" * 60,
                 "mp3 then garbage": mp3_frame() + b"garbage!" * 10, "bad id3": b"ID3\x04\x00\x00\x80\x00\x00\x00"}
        for name, data in cases.items():
            with self.subTest(case=name), self.assertRaises((ValueError, OSError)):
                self.check(data)
        with self.assertRaises(ValueError):
            pcm_to_wav(b"\x00", sample_rate=24000)

    def test_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big"
            path.write_bytes(pcm_to_wav(b"\x00\x00" * 1000, sample_rate=24000))
            with self.assertRaises(ValueError):
                inspect_audio(path, max_bytes=100)


if __name__ == "__main__":
    unittest.main()
