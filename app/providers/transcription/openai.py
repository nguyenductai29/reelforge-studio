"""OpenAI speech-to-text (``POST /v1/audio/transcriptions``) with segment timestamps.

Contract: https://platform.openai.com/docs/api-reference/audio/createTranscription
``whisper-1`` with ``response_format=verbose_json`` returns the text, the detected
language, the duration and timestamped segments. Files are at most 25 MB; the
worker sends a compact mono MP3 extracted with FFmpeg.
"""
from pathlib import Path

import httpx

from app.providers.errors import response_detail, status_error
from app.providers.transcription.base import (TranscriptionModel, TranscriptionProvider, TranscriptionProviderError,
                                              TranscriptResult, TranscriptSegment)

API_URL = "https://api.openai.com/v1/audio/transcriptions"
TRANSCRIPTION_MODELS = {"whisper-1": TranscriptionModel("whisper-1")}
_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".webm": "audio/webm",
         ".mp4": "audio/mp4"}


class OpenAITranscriptionProvider(TranscriptionProvider):
    name = "openai"
    models = TRANSCRIPTION_MODELS

    def _transcribe(self, path: Path, model: str, language: str | None) -> TranscriptResult:
        data = {"model": model, "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
        if language:
            data["language"] = language
        try:
            with path.open("rb") as audio:
                response = self.http_client.post(API_URL, headers={"Authorization": f"Bearer {self._api_key}"},
                                                 data=data, files={"file": (path.name, audio, _MIME[path.suffix.lower()])},
                                                 follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise TranscriptionProviderError("submission_unknown", "openai did not answer in time",
                                             category="timeout") from exc
        except httpx.RequestError as exc:
            raise TranscriptionProviderError("submission_unknown", "Lost the connection to openai",
                                             category="network_error") from exc
        if not 200 <= response.status_code < 300:
            code, retryable = status_error(response.status_code)
            raise TranscriptionProviderError(code, f"openai returned HTTP {response.status_code}", retryable=retryable,
                                             http_status=response.status_code, provider_detail=response_detail(response))
        try:
            body = response.json()
        except ValueError as exc:
            raise TranscriptionProviderError("invalid_response", "openai returned invalid JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise TranscriptionProviderError("invalid_response", "openai returned no transcript")
        segments = []
        for item in body.get("segments") or []:
            try:
                start, end, text = float(item["start"]), float(item["end"]), str(item["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if text and 0 <= start <= end:
                segments.append(TranscriptSegment(round(start, 3), round(end, 3), text))
        if not body["text"].strip() and not segments:
            raise TranscriptionProviderError("empty_output", "openai found no speech")
        duration = body.get("duration")
        language = body.get("language")
        return TranscriptResult(self.name, model, body["text"].strip(),
                                language[:20] if isinstance(language, str) else None,
                                float(duration) if isinstance(duration, (int, float)) else None, tuple(segments),
                                (response.headers.get("x-request-id") or "")[:200] or None)
