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
            result, _, _ = await OpenRouterProvider()._post_openrouter(
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
            result, _, _ = await OpenRouterProvider()._post_openrouter(
                model="test/model",
                system_prompt="system",
                messages=[{"role": "user", "content": "meal"}],
            )
    finally:
        await client.aclose()

    assert result == "{}"
    assert attempts == 2
