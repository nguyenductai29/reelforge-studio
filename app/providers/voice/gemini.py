"""Google Gemini API text-to-speech (generateContent with an audio response).

Contract: https://ai.google.dev/gemini-api/docs/speech-generation
The key goes in the x-goog-api-key header, never in the URL. The response
carries the speech inline as base64 PCM (``audio/L16;codec=pcm;rate=24000``:
16-bit little-endian mono); it is wrapped in a WAV container here. Gemini
detects the language from the text and takes delivery directions in natural
language, so ``style`` becomes a short instruction before the text. It has no
speed, pitch or output-format parameter.
"""
import base64
import binascii
import re

import httpx

from app.audio_files import pcm_to_wav
from app.providers.errors import response_detail, status_error
from app.providers.voice.base import (STYLES, VoiceGenerationProvider, VoiceModel, VoiceProviderError, VoiceRequest,
                                      VoiceResult)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
VOICES = ("Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
          "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia",
          "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix",
          "Sadachbia", "Sadaltager", "Sulafat")
DEFAULT_VOICE = "Kore"
VOICE_MODELS = {model: VoiceModel(model, VOICES, formats=("wav",), styles=STYLES, max_text_chars=5000)
                for model in ("gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts")}
_DIRECTIONS = {"neutral": "", "calm": "Say calmly:", "cheerful": "Say cheerfully:",
               "energetic": "Say with energy and enthusiasm:", "serious": "Say in a serious tone:",
               "slow": "Say slowly and clearly:"}
_RATE = re.compile(r"rate=(\d{4,6})")
_FILTERED = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"})


def _error(status: int, detail: str | None) -> VoiceProviderError:
    code, retryable = status_error(status)
    return VoiceProviderError(code, f"gemini returned HTTP {status}", retryable=retryable, http_status=status,
                              provider_detail=detail)


class GeminiVoiceProvider(VoiceGenerationProvider):
    name = "gemini"
    models = VOICE_MODELS

    def _generate(self, request: VoiceRequest) -> VoiceResult:
        direction = _DIRECTIONS[request.style]
        text = request.text.strip()
        payload = {"contents": [{"role": "user", "parts": [{"text": f"{direction} {text}" if direction else text}]}],
                   "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                       "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": request.voice}}}}}
        try:
            response = self.http_client.post(f"{API_BASE}/{request.model}:generateContent",
                                             headers={"x-goog-api-key": self._api_key}, json=payload,
                                             follow_redirects=False)
        except httpx.TimeoutException as exc:
            # The request may have been processed (and billed) after it was sent.
            raise VoiceProviderError("submission_unknown", "gemini did not answer in time", category="timeout") from exc
        except httpx.RequestError as exc:
            raise VoiceProviderError("submission_unknown", "Lost the connection to gemini",
                                     category="network_error") from exc
        if not 200 <= response.status_code < 300:
            raise _error(response.status_code, response_detail(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise VoiceProviderError("invalid_response", "gemini returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise VoiceProviderError("invalid_response", "gemini returned an invalid response")
        feedback = data.get("promptFeedback") if isinstance(data.get("promptFeedback"), dict) else {}
        if feedback.get("blockReason"):
            raise VoiceProviderError("content_rejected", "gemini blocked the text")
        candidates = data.get("candidates")
        candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
        content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
        parts = [part.get("inlineData") for part in content.get("parts") or [] if isinstance(part, dict)]
        inline = [part for part in parts if isinstance(part, dict) and isinstance(part.get("data"), str)]
        if not inline:
            if candidate.get("finishReason") in _FILTERED:
                raise VoiceProviderError("content_rejected", "gemini declined to read this text")
            raise VoiceProviderError("empty_output", "gemini returned no audio")
        audio, content_type, duration = self._audio(inline)
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        response_id = data.get("responseId") if isinstance(data.get("responseId"), str) else None
        version = data.get("modelVersion") if isinstance(data.get("modelVersion"), str) else request.model
        return VoiceResult(self.name, version[:100], audio, content_type, duration,
                           response_id[:200] if response_id else None,
                           {key: usage.get(key) for key in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")
                            if isinstance(usage.get(key), int)})

    @staticmethod
    def _audio(parts: list[dict]) -> tuple[bytes, str, float | None]:
        """Raw PCM parts joined into one WAV file; the rate comes from the MIME type (24 kHz by default)."""
        mime = str(parts[0].get("mimeType") or "").lower()
        try:
            chunks = [base64.b64decode(part["data"], validate=True) for part in parts]
        except (binascii.Error, ValueError) as exc:
            raise VoiceProviderError("invalid_response", "gemini returned invalid audio data") from exc
        if mime.startswith(("audio/l16", "audio/pcm")):
            match = _RATE.search(mime)
            rate = int(match.group(1)) if match else 24000
            pcm = b"".join(chunks)
            try:
                return pcm_to_wav(pcm, sample_rate=rate), "audio/wav", round(len(pcm) / (rate * 2), 3)
            except ValueError as exc:
                raise VoiceProviderError("invalid_response", "gemini returned invalid PCM audio") from exc
        if mime in ("audio/wav", "audio/x-wav") and len(chunks) == 1:
            return chunks[0], "audio/wav", None
        raise VoiceProviderError("invalid_response", "gemini returned an unexpected audio type")
