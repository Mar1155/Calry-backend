import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.ai.errors import SpeechTranscriptionError
from app.ai.providers.openrouter import OPENROUTER_TRANSCRIPTIONS_URL, OpenRouterProvider
from app.core.config import settings


@pytest.mark.asyncio
async def test_transcription_uses_dedicated_stt_endpoint_then_returns_text() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                content=b"audio bytes",
                headers={"content-type": "audio/mp4"},
            )
        return httpx.Response(
            200,
            json={
                "text": "Ho mangiato pasta al pomodoro",
                "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "OPENROUTER_TRANSCRIPTION_MODEL", "openai/whisper-large-v3"),
            patch.object(settings, "AI_MAX_RETRIES", 0),
        ):
            result = await OpenRouterProvider().transcribe_audio("https://example.com/recording.m4a?signature=123")
    finally:
        await client.aclose()

    assert len(requests) == 2
    assert str(requests[1].url) == OPENROUTER_TRANSCRIPTIONS_URL
    payload = json.loads(requests[1].content)
    assert payload == {
        "model": "openai/whisper-large-v3",
        "input_audio": {
            "data": base64.b64encode(b"audio bytes").decode("utf-8"),
            "format": "m4a",
        },
        "temperature": 0,
    }
    assert result.transcript == "Ho mangiato pasta al pomodoro"
    assert result.model_name == "openai/whisper-large-v3"
    assert result.token_usage == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
        "cached_tokens": None,
    }


@pytest.mark.asyncio
async def test_transcription_retries_temporary_stt_failure() -> None:
    post_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        if request.method == "GET":
            return httpx.Response(200, content=b"audio", headers={"content-type": "audio/wav"})
        post_attempts += 1
        if post_attempts == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, json={"text": "una mela"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch("app.ai.providers.openrouter.asyncio.sleep", new_callable=AsyncMock),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "AI_MAX_RETRIES", 1),
        ):
            result = await OpenRouterProvider().transcribe_audio("https://example.com/audio.wav")
    finally:
        await client.aclose()

    assert post_attempts == 2
    assert result.transcript == "una mela"


@pytest.mark.asyncio
async def test_transcription_rejects_empty_stt_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=b"audio", headers={"content-type": "audio/mpeg"})
        return httpx.Response(200, json={"text": "  "})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "AI_MAX_RETRIES", 0),
        ):
            with pytest.raises(SpeechTranscriptionError):
                await OpenRouterProvider().transcribe_audio("https://example.com/audio.mp3")
    finally:
        await client.aclose()
