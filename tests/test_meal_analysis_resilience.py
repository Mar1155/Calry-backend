import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.ai.errors import AIInvalidResponseError, AIProviderError, ImageAnalysisError
from app.ai.providers.openrouter import OpenRouterProvider
from app.core.config import settings
from app.tasks.meal_analysis import _is_retryable_failure


def test_retry_classifier_retries_transient_provider_failures() -> None:
    error = AIProviderError(details={"retryable": True, "reason": "ReadTimeout"})
    assert _is_retryable_failure(error) is True


def test_retry_classifier_rejects_permanent_input_failures() -> None:
    image_error = ImageAnalysisError(details={"retryable": False})
    assert _is_retryable_failure(image_error) is False
    assert _is_retryable_failure(AIInvalidResponseError()) is False
    assert _is_retryable_failure(ValueError("invalid payload")) is False


def test_retry_classifier_bounds_unknown_infrastructure_failures() -> None:
    assert _is_retryable_failure(ConnectionError("redis unavailable")) is True


@pytest.mark.asyncio
async def test_provider_downgrade_does_not_consume_retry_budget() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="structured output unsupported")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "AI_MAX_RETRIES", 0),
        ):
            result, _, _, _ = await OpenRouterProvider()._post_openrouter(
                model="test/model",
                system_prompt="system",
                messages=[{"role": "user", "content": "meal"}],
                response_format={"type": "json_schema", "json_schema": {"name": "meal"}},
            )
    finally:
        await client.aclose()

    assert result == "{}"
    assert [request["response_format"]["type"] for request in requests] == ["json_schema", "json_object"]


@pytest.mark.asyncio
async def test_provider_retries_transient_http_failure() -> None:
    attempts = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch("app.ai.providers.openrouter.asyncio.sleep", new_callable=AsyncMock),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "AI_MAX_RETRIES", 1),
        ):
            result, _, _, _ = await OpenRouterProvider()._post_openrouter(
                model="test/model",
                system_prompt="system",
                messages=[{"role": "user", "content": "meal"}],
            )
    finally:
        await client.aclose()

    assert result == "{}"
    assert attempts == 2


@pytest.mark.asyncio
async def test_post_openrouter_surfaces_finish_reason() -> None:
    """C26: a completion cut off by max_completion_tokens must be identifiable
    by the caller, not just silently swallowed."""

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
        ):
            _, _, _, finish_reason = await OpenRouterProvider()._post_openrouter(
                model="test/model",
                system_prompt="system",
                messages=[{"role": "user", "content": "meal"}],
            )
    finally:
        await client.aclose()

    assert finish_reason == "length"


@pytest.mark.asyncio
async def test_truncated_completion_is_marked_degraded() -> None:
    """A truncated response that still happens to parse as valid JSON must not
    be trusted at full confidence — finish_reason=length forces degraded_extraction."""
    provider = OpenRouterProvider()
    complete_json = json.dumps(
        {
            "meal_name": "Pizza",
            "estimated_calories": 800,
            "estimated_min_calories": 700,
            "estimated_max_calories": 900,
            "meal_category_suggestion": None,
            "meal_category_confidence": None,
            "items": [
                {
                    "name": "Dough",
                    "quantity_estimate": "200 g",
                    "weight_grams": 200,
                    "calories_per_100g": 270.0,
                    "protein_g": 10.0,
                    "carbs_g": 40.0,
                    "fat_g": 3.0,
                }
            ],
            "assumptions": [],
            "needs_clarification": False,
            "clarifying_question": None,
        }
    )
    result = await provider._parse_and_build_meal(
        complete_json,
        latency_ms=100,
        usage=None,
        source_type="photo",
        model="test/model",
        prompt_version="test",
        finish_reason="length",
    )
    assert result.degraded_extraction is True
    assert result.finish_reason == "length"
