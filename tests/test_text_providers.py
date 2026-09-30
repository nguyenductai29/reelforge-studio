"""Text provider adapters: request shape, normalized results and errors, with mocked HTTP."""
import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.providers.text import (TEXT_PROVIDERS, TextProviderError, create_text_provider, text_credit_cost,
                                text_provider_config_issue)
from app.providers.text.anthropic import AnthropicTextProvider
from app.providers.text.gemini import GeminiTextProvider
from app.providers.text.openai import OpenAITextProvider

KEY = "sk-test-secret-key"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def respond(status=200, body=None, record=None):
    def handler(request):
        if record is not None:
            record.append(request)
        return httpx.Response(status, json=body if body is not None else {})
    return handler


class OpenAITest(unittest.TestCase):
    BODY = {"id": "chatcmpl-1", "model": "gpt-4.1-mini-2025-04-14",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "  Xin chào  "},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}}

    def test_request_and_normalized_result(self):
        seen = []
        provider = OpenAITextProvider(KEY, http_client=client(respond(body=self.BODY, record=seen)))
        result = provider.generate(model="gpt-4.1-mini", prompt="Viết lời chào", system_prompt="Be brief",
                                   max_tokens=200)
        request = seen[0]
        self.assertEqual(str(request.url), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], f"Bearer {KEY}")
        body = json.loads(request.content)
        self.assertEqual(body, {"model": "gpt-4.1-mini", "max_completion_tokens": 200,
                                "messages": [{"role": "system", "content": "Be brief"},
                                             {"role": "user", "content": "Viết lời chào"}]})
        self.assertEqual((result.text, result.provider, result.model), ("Xin chào", "openai", "gpt-4.1-mini-2025-04-14"))
        self.assertEqual(result.usage.as_dict(), {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17})
        self.assertEqual(result.raw_metadata, {"response_id": "chatcmpl-1", "finish_reason": "stop",
                                               "provider_finish_reason": "stop"})

    def test_optional_temperature_and_json_format(self):
        seen = []
        provider = OpenAITextProvider(KEY, http_client=client(respond(body=self.BODY, record=seen)))
        provider.generate(model="gpt-4.1-mini", prompt="x", temperature=0.2, response_format="json")
        body = json.loads(seen[0].content)
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn("JSON", body["messages"][0]["content"])

    def test_refusal_is_a_content_block(self):
        refused = {**self.BODY, "choices": [{"message": {"content": None, "refusal": "No."}, "finish_reason": "stop"}]}
        provider = OpenAITextProvider(KEY, http_client=client(respond(body=refused)))
        with self.assertRaises(TextProviderError) as caught:
            provider.generate(model="gpt-4.1-mini", prompt="x")
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("content_blocked", False))


class AnthropicTest(unittest.TestCase):
    BODY = {"id": "msg_1", "type": "message", "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Phần một. "}, {"type": "tool_use", "id": "x"},
                        {"type": "text", "text": "Phần hai."}],
            "usage": {"input_tokens": 30, "output_tokens": 9}}

    def test_request_and_normalized_result(self):
        seen = []
        provider = AnthropicTextProvider(KEY, http_client=client(respond(body=self.BODY, record=seen)))
        result = provider.generate(model="claude-sonnet-5-5", prompt="Viết", system_prompt="Be brief",
                                   temperature=0.5, max_tokens=300)
        request = seen[0]
        self.assertEqual(str(request.url), "https://api.anthropic.com/v1/messages")
        self.assertEqual((request.headers["x-api-key"], request.headers["anthropic-version"]), (KEY, "2023-06-01"))
        self.assertNotIn("authorization", request.headers)
        self.assertEqual(json.loads(request.content),
                         {"model": "claude-sonnet-5-5", "max_tokens": 300, "system": "Be brief", "temperature": 0.5,
                          "messages": [{"role": "user", "content": "Viết"}]})
        self.assertEqual((result.text, result.provider, result.model), ("Phần một. Phần hai.", "anthropic", "claude-sonnet-5-5"))
        self.assertEqual(result.usage.as_dict(), {"input_tokens": 30, "output_tokens": 9, "total_tokens": 39})
        self.assertEqual(result.raw_metadata["finish_reason"], "stop")

    def test_token_limit_without_text_is_an_error(self):
        cut = {**self.BODY, "stop_reason": "max_tokens", "content": []}
        provider = AnthropicTextProvider(KEY, http_client=client(respond(body=cut)))
        with self.assertRaises(TextProviderError) as caught:
            provider.generate(model="claude-sonnet-5-5", prompt="x")
        self.assertEqual(caught.exception.code, "empty_output")


class GeminiTest(unittest.TestCase):
    BODY = {"responseId": "resp-1", "modelVersion": "gemini-2.5-flash-001",
            "candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
                {"text": "thinking…", "thought": True}, {"text": "Kết quả"}]}}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 25}}

    def test_request_and_normalized_result(self):
        seen = []
        provider = GeminiTextProvider(KEY, http_client=client(respond(body=self.BODY, record=seen)))
        result = provider.generate(model="models/gemini-2.5-flash", prompt="Viết", system_prompt="Be brief",
                                   max_tokens=400, response_format="json")
        request = seen[0]
        self.assertEqual(str(request.url),
                         "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
        self.assertEqual(request.headers["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, str(request.url))
        body = json.loads(request.content)
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "Viết"}]}])
        self.assertEqual(body["generationConfig"], {"maxOutputTokens": 400, "responseMimeType": "application/json"})
        self.assertTrue(body["systemInstruction"]["parts"][0]["text"].startswith("Be brief"))
        self.assertEqual((result.text, result.model), ("Kết quả", "gemini-2.5-flash-001"))
        self.assertEqual(result.usage.as_dict(), {"input_tokens": 7, "output_tokens": 3, "total_tokens": 25})

    def test_blocked_prompt_and_unsafe_model_name(self):
        provider = GeminiTextProvider(KEY, http_client=client(respond(body={"promptFeedback": {"blockReason": "SAFETY"}})))
        with self.assertRaises(TextProviderError) as caught:
            provider.generate(model="gemini-2.5-flash", prompt="x")
        self.assertEqual(caught.exception.code, "content_blocked")
        seen = []
        provider = GeminiTextProvider(KEY, http_client=client(respond(body=self.BODY, record=seen)))
        with self.assertRaises(TextProviderError) as caught:
            provider.generate(model="../other:generateContent", prompt="x")
        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(seen, [])


class ErrorAndConfigTest(unittest.TestCase):
    def test_http_status_maps_to_stable_codes_without_secrets(self):
        cases = [(400, "invalid_request", False), (401, "auth_error", False), (402, "billing_error", False),
                 (404, "not_found", False), (429, "rate_limited", True), (503, "provider_unavailable", True),
                 (529, "provider_unavailable", True)]
        for status, code, retryable in cases:
            for provider_type in (OpenAITextProvider, AnthropicTextProvider, GeminiTextProvider):
                with self.subTest(status=status, provider=provider_type.name):
                    body = {"error": {"message": f"bad key {KEY}"}}
                    provider = provider_type(KEY, http_client=client(respond(status, body)))
                    with self.assertRaises(TextProviderError) as caught:
                        provider.generate(model="model-1", prompt="x")
                    self.assertEqual((caught.exception.code, caught.exception.retryable, caught.exception.http_status),
                                     (code, retryable, status))
                    self.assertNotIn(KEY, str(caught.exception))

    def test_transport_problems_are_retryable(self):
        def unreachable(request):
            raise httpx.ConnectError("down", request=request)

        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        for handler, code in ((unreachable, "transport_error"), (slow, "timeout")):
            provider = OpenAITextProvider(KEY, http_client=client(handler))
            with self.assertRaises(TextProviderError) as caught:
                provider.generate(model="gpt-4.1-mini", prompt="x")
            self.assertEqual((caught.exception.code, caught.exception.retryable), (code, True))

        def not_json(request):
            return httpx.Response(200, content=b"<html>")

        with self.assertRaises(TextProviderError) as caught:
            OpenAITextProvider(KEY, http_client=client(not_json)).generate(model="gpt-4.1-mini", prompt="x")
        self.assertEqual((caught.exception.code, caught.exception.retryable), ("provider_response", False))

    def test_requests_are_validated_before_any_call(self):
        seen = []
        provider = OpenAITextProvider(KEY, http_client=client(respond(body=OpenAITest.BODY, record=seen)))
        for kwargs in ({"prompt": "  "}, {"prompt": "x", "temperature": 3}, {"prompt": "x", "max_tokens": 0},
                       {"prompt": "x", "response_format": "xml"}, {"prompt": "x", "model": "bad model"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(TextProviderError) as caught:
                    provider.generate(**{"model": "gpt-4.1-mini", **kwargs})
                self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(seen, [])

    def test_missing_api_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": ""}):
            for name, spec in TEXT_PROVIDERS.items():
                self.assertEqual(text_provider_config_issue(name), ("missing_key", f"Server cần {spec.key_env}."))
                with self.assertRaises(TextProviderError) as caught:
                    create_text_provider(name)
                self.assertEqual((caught.exception.code, caught.exception.retryable), ("missing_key", False))
        self.assertEqual(text_provider_config_issue("mistral")[0], "unsupported_provider")
        with self.assertRaises(TextProviderError) as caught:
            OpenAITextProvider("  ")
        self.assertEqual(caught.exception.code, "invalid_config")
        with patch.dict(os.environ, {"GEMINI_API_KEY": KEY}):
            self.assertIsNone(text_provider_config_issue("gemini"))
            provider = create_text_provider("gemini")
            self.assertIsInstance(provider, GeminiTextProvider)
            provider.close()

    def test_credit_cost_is_configurable(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEXT_CREDITS_PER_GENERATION", None)
            self.assertEqual(text_credit_cost(), 1)
        with patch.dict(os.environ, {"TEXT_CREDITS_PER_GENERATION": "3"}):
            self.assertEqual(text_credit_cost(), 3)
        for bad in ("0", "abc", "100001"):
            with patch.dict(os.environ, {"TEXT_CREDITS_PER_GENERATION": bad}), self.assertRaises(RuntimeError):
                text_credit_cost()


if __name__ == "__main__":
    unittest.main()
