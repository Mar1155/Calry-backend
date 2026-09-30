"""OpenRouter can fail *after* answering 200 on a stream: it sends an in-band
error chunk with finish_reason "error". That fragment must never be parsed as
a finished estimate (it became a made-up one-item meal, or a failed scan that
worked on retry)."""
import json
from unittest.mock import patch

import httpx
import pytest

from app.ai.errors import AIProviderError
from app.ai.providers import openrouter
from app.ai.providers.openrouter import OpenRouterProvider


def _sse(*chunks: dict) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def _delta(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


_ERROR = {"error": {"code": 502, "message": "upstream provider error"}, "choices": [{"delta": {}, "finish_reason": "error"}]}
_STOP = {"choices": [{"delta": {}, "finish_reason": "stop"}]}


async def _collect(bodies: list[bytes]) -> tuple[list[dict], int]:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        body = bodies[min(calls, len(bodies) - 1)]
        calls += 1
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider()
    events: list[dict] = []
    with (
        patch.object(openrouter, "get_shared_client", lambda: client),
        patch.object(OpenRouterProvider, "_get_api_key", lambda self: "test-key"),
        patch.object(OpenRouterProvider, "_retry_delay", staticmethod(lambda *a, **k: 0)),
    ):
        try:
            async for ev in provider.stream_chat(model="m", system_prompt="s", messages=[]):
                events.append(ev)
        finally:
            await client.aclose()
    return events, calls


@pytest.mark.asyncio
async def test_mid_stream_error_after_content_raises_retryable():
    with pytest.raises(AIProviderError) as exc:
        await _collect([_sse(_delta('{"meal_name": "Pizza spe'), _ERROR)])
    assert exc.value.details == {"retryable": True, "reason": "stream_error"}


@pytest.mark.asyncio
async def test_error_before_any_content_retries_the_stream():
    events, calls = await _collect([_sse(_ERROR), _sse(_delta('{"meal_name": "Pizza"}'), _STOP)])
    assert calls == 2
    assert events[-1]["meta"]["raw_text"] == '{"meal_name": "Pizza"}'
    assert events[-1]["meta"]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_a_clean_stream_is_unchanged():
    events, calls = await _collect([_sse(_delta('{"a"'), _delta(": 1}"), _STOP)])
    assert calls == 1
    assert [e["delta"] for e in events if "delta" in e] == ['{"a"', ": 1}"]
    assert events[-1]["meta"]["finish_reason"] == "stop"
