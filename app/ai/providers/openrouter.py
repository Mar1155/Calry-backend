import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any

import httpx
from pydantic import ValidationError

from app.ai.errors import (
    AIInvalidResponseError,
    AIProviderError,
    ImageAnalysisError,
    SpeechTranscriptionError,
)
from app.ai.prompts.image_estimation import (
    IMAGE_MEAL_ESTIMATION_PROMPT_VERSION,
    IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT,
    build_image_meal_estimation_user_text,
)
from app.ai.prompts.meal_completion import (
    MEAL_COMPLETION_PROMPT_VERSION,
    MEAL_COMPLETION_SYSTEM_PROMPT,
)
from app.ai.prompts.meal_estimation import (
    JSON_REPAIR_SYSTEM_PROMPT,
    TEXT_MEAL_ESTIMATION_PROMPT_VERSION,
    TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT,
    build_text_meal_estimation_user_prompt,
)
from app.ai.prompts.meal_refinement import (
    MEAL_REFINEMENT_PROMPT_VERSION,
    MEAL_REFINEMENT_SYSTEM_PROMPT,
    build_meal_refinement_user_prompt,
)
from app.ai.prompts.voice_transcription import (
    VOICE_TRANSCRIPTION_SYSTEM_PROMPT,
)
from app.ai.providers.base import BaseAIProvider
from app.ai.schemas.meal_completion import MealCompletionRequest, MealCompletionResult, MealSuggestionItem
from app.ai.schemas.meal_estimate import (
    MEAL_ESTIMATE_RESPONSE_SCHEMA,
    MEAL_REFINEMENT_RESPONSE_SCHEMA,
    MealEstimateItem,
    MealEstimateResult,
    SpeechTranscriptionResult,
    UserContext,
)
from app.core.config import settings

logger = logging.getLogger("app.ai.openrouter")

_LANGUAGE_NAMES = {
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
    "zh": "Chinese",
    "ja": "Japanese",
    "ar": "Arabic",
}

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}

# C7: a single connection-pooled client reused across all calls (and across the
# retry loop), instead of a fresh TLS handshake per attempt / per media fetch.
_shared_client: httpx.AsyncClient | None = None


def get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
    return _shared_client


async def close_shared_client() -> None:
    """Closed on application shutdown (see main.py lifespan)."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


class OpenRouterProvider(BaseAIProvider):
    """OpenRouter API model provider implementation using OpenAI-compatible endpoints."""

    @property
    def provider_name(self) -> str:
        return "openrouter"

    def _get_api_key(self) -> str:
        key = settings.OPENROUTER_API_KEY
        if not key or key.startswith("your-") or "api-key" in key.lower():
            raise AIProviderError("OPENROUTER_API_KEY is not configured or is a placeholder.")
        return key

    @staticmethod
    def _build_user_context(user_context: UserContext | None, *, leading_blank: bool = False) -> str:
        if not user_context:
            return ""

        context_parts = []
        if user_context.locale:
            primary = user_context.locale.split("-")[0].split("_")[0].strip().lower()
            output_lang = _LANGUAGE_NAMES.get(primary, "English")
            context_parts.append(
                "Output Language: "
                f"{output_lang}. Write all free-text JSON fields in {output_lang}: "
                "meal_name, item names, quantity_estimate, assumptions, "
                "clarifying_question, ai_summary, and changes_made. Keep enum "
                "values such as confidence/source_type in lowercase English exactly "
                "as specified by the schema."
            )
        if user_context.sex or user_context.age or user_context.height_cm or user_context.weight_kg:
            profile = []
            if user_context.sex:
                profile.append(f"Sex: {user_context.sex}")
            if user_context.age:
                profile.append(f"Age: {user_context.age}")
            if user_context.height_cm:
                profile.append(f"Height: {user_context.height_cm} cm")
            if user_context.weight_kg:
                profile.append(f"Weight: {user_context.weight_kg} kg")
            context_parts.append(f"User Physical Profile: {', '.join(profile)}")
        if user_context.daily_calorie_goal:
            context_parts.append(f"User Daily Calorie Goal: {user_context.daily_calorie_goal} kcal")
        if user_context.previous_corrections_summary:
            context_parts.append(
                "User Recent Meal Confirmations (meal-specific reference only):\n"
                f"{user_context.previous_corrections_summary}\n"
                "Use a prior value only when it clearly describes the same or a "
                "closely equivalent meal. Do not apply the aggregate correction "
                "percentage: systematic bias is handled deterministically after inference."
            )
        if not context_parts:
            return ""

        prefix = "\n\n" if leading_blank else ""
        return f"{prefix}USER CONTEXT:\n" + "\n".join(context_parts)

    # ---- transport ----------------------------------------------------------

    @staticmethod
    def _response_format(schema: dict | None = None, name: str = "response") -> dict:
        """C16: prefer json_schema structured output when enabled, else json_object."""
        if schema is not None and settings.AI_STRUCTURED_OUTPUT:
            return {"type": "json_schema", "json_schema": {"name": name, "strict": False, "schema": schema}}
        return {"type": "json_object"}

    @staticmethod
    def _normalize_usage(usage: dict | None) -> dict | None:
        """Extract prompt/completion/cached token counts from an OpenRouter usage block."""
        if not usage:
            return None
        details = usage.get("prompt_tokens_details") or {}
        return {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cached_tokens": details.get("cached_tokens") if isinstance(details, dict) else None,
        }

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if not value:
            return None
        try:
            return min(float(value), 10.0)
        except ValueError:
            return None

    @staticmethod
    def _retry_delay(attempt: int, retry_after: float | None = None) -> float:
        return retry_after if retry_after is not None else min(4.0, 0.5 * (2**attempt))

    async def _post_openrouter(
        self,
        model: str,
        system_prompt: str,
        messages: list,
        response_format: dict | None = None,
    ) -> tuple[str, int, dict | None]:
        """Performs a POST to OpenRouter. Returns (text, latency_ms, usage)."""
        api_key = self._get_api_key()

        full_messages = [{"role": "system", "content": system_prompt}] + messages
        payload: dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            "max_completion_tokens": settings.AI_MAX_COMPLETION_TOKENS,
            "temperature": settings.AI_TEMPERATURE,
            "reasoning": {
                "effort": settings.AI_REASONING_EFFORT,
                "exclude": settings.AI_EXCLUDE_REASONING,
            },
        }
        if response_format:
            payload["response_format"] = response_format
            if response_format.get("type") == "json_schema":
                # Only route to providers that honour the structured-output param.
                payload["provider"] = {"require_parameters": True}

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://calry.ai",
            "X-Title": "Calry",
        }

        timeout = float(settings.AI_REQUEST_TIMEOUT_SECONDS)
        max_retries = int(settings.AI_MAX_RETRIES)
        client = get_shared_client()
        downgraded = False

        attempt = 0
        while attempt <= max_retries:
            try:
                start_time = time.perf_counter()
                response = await client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=timeout)
                latency_ms = int((time.perf_counter() - start_time) * 1000)
            except httpx.RequestError as e:
                logger.warning(f"OpenRouter API request error on attempt {attempt + 1}: {e}")
                if attempt == max_retries:
                    raise AIProviderError(
                        details={"retryable": True, "reason": type(e).__name__},
                    )
                await asyncio.sleep(self._retry_delay(attempt))
                attempt += 1
                continue

            if response.status_code == 200:
                try:
                    res_json = response.json()
                    text_out = res_json["choices"][0]["message"]["content"]
                    if not isinstance(text_out, str) or not text_out.strip():
                        raise ValueError("empty completion content")
                    return text_out, latency_ms, res_json.get("usage")
                except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    logger.warning("Malformed OpenRouter response on attempt %s: %s", attempt + 1, exc)
                    if attempt == max_retries:
                        raise AIProviderError(
                            "AI service returned an unreadable response. Please try again.",
                            details={"retryable": True, "reason": type(exc).__name__},
                        )
                    await asyncio.sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue

            # C16 graceful degrade: a routed model may reject json_schema.
            if (
                response.status_code in (400, 404, 422)
                and not downgraded
                and isinstance(payload.get("response_format"), dict)
                and payload["response_format"].get("type") == "json_schema"
            ):
                logger.warning(
                    f"Structured output rejected ({response.status_code}); "
                    f"falling back to json_object for model {model}."
                )
                payload["response_format"] = {"type": "json_object"}
                payload.pop("provider", None)
                downgraded = True
                continue

            logger.warning(
                "OpenRouter API attempt %s failed: status=%s body=%s",
                attempt + 1,
                response.status_code,
                response.text[:500],
            )
            if response.status_code not in _RETRYABLE_STATUS_CODES:
                raise AIProviderError(
                    "AI service rejected the request. Please try a different input.",
                    details={"retryable": False, "status_code": response.status_code},
                )
            if attempt == max_retries:
                raise AIProviderError(
                    details={"retryable": True, "status_code": response.status_code},
                )
            await asyncio.sleep(self._retry_delay(attempt, self._retry_after_seconds(response)))
            attempt += 1

        raise AIProviderError("OpenRouter API call failed after retries.")

    async def stream_chat(
        self,
        model: str,
        system_prompt: str,
        messages: list,
        response_format: dict | None = None,
    ) -> "AsyncIterator[dict]":
        """Streaming transport. Async-generates ``{"delta": str}`` items as the
        model produces content, then a final ``{"meta": {"usage", "latency_ms",
        "raw_text"}}`` item. Raises AIProviderError on transport/HTTP failure.

        Applies the same json_schema -> json_object graceful downgrade as
        ``_post_openrouter`` (one retry). No network-error retry: a stream that
        drops mid-flight surfaces to the caller, which falls back to a full parse
        of whatever text arrived."""
        api_key = self._get_api_key()
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        payload: dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": settings.AI_MAX_COMPLETION_TOKENS,
            "temperature": settings.AI_TEMPERATURE,
            "reasoning": {
                "effort": settings.AI_REASONING_EFFORT,
                "exclude": settings.AI_EXCLUDE_REASONING,
            },
        }
        if response_format:
            payload["response_format"] = response_format
            if response_format.get("type") == "json_schema":
                payload["provider"] = {"require_parameters": True}

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://calry.ai",
            "X-Title": "Calry",
        }
        timeout = float(settings.AI_REQUEST_TIMEOUT_SECONDS)
        client = get_shared_client()
        downgraded = False
        attempt = 0
        max_retries = int(settings.AI_MAX_RETRIES)

        while True:
            start_time = time.perf_counter()
            parts: list[str] = []
            usage: dict | None = None
            emitted_content = False
            try:
                async with client.stream(
                    "POST", OPENROUTER_URL, json=payload, headers=headers, timeout=timeout
                ) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode(errors="replace")
                        if (
                            response.status_code in (400, 404, 422)
                            and not downgraded
                            and isinstance(payload.get("response_format"), dict)
                            and payload["response_format"].get("type") == "json_schema"
                        ):
                            logger.warning(
                                f"Streaming structured output rejected ({response.status_code}); "
                                f"falling back to json_object for model {model}."
                            )
                            payload["response_format"] = {"type": "json_object"}
                            payload.pop("provider", None)
                            downgraded = True
                            continue
                        logger.warning(
                            "OpenRouter stream failed: status=%s body=%s",
                            response.status_code,
                            body[:500],
                        )
                        if response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                            retry_after = self._retry_after_seconds(response)
                            await asyncio.sleep(self._retry_delay(attempt, retry_after))
                            attempt += 1
                            continue
                        raise AIProviderError(
                            details={
                                "retryable": response.status_code in _RETRYABLE_STATUS_CODES,
                                "status_code": response.status_code,
                            },
                        )

                    async for raw in response.aiter_lines():
                        if not raw:
                            continue
                        stripped = raw.strip()
                        if not stripped.startswith("data:"):
                            continue  # SSE comments / keep-alives
                        data = stripped[len("data:") :].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk.get("usage"), dict):
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            content = delta.get("content")
                            if content:
                                parts.append(content)
                                emitted_content = True
                                yield {"delta": content}

                latency_ms = int((time.perf_counter() - start_time) * 1000)
                yield {"meta": {"usage": usage, "latency_ms": latency_ms, "raw_text": "".join(parts)}}
                return
            except httpx.RequestError as e:
                logger.warning(f"OpenRouter stream request error: {e}")
                if not emitted_content and attempt < max_retries:
                    await asyncio.sleep(self._retry_delay(attempt))
                    attempt += 1
                    continue
                raise AIProviderError(
                    details={"retryable": True, "reason": type(e).__name__},
                )

    def stream_meal_from_text(
        self,
        input_text: str,
        user_context: UserContext | None = None,
        is_voice: bool = False,
        additional_context: str | None = None,
    ) -> "AsyncIterator[dict]":
        messages = self._build_text_messages(input_text, user_context, is_voice, additional_context)
        return self.stream_chat(
            model=settings.OPENROUTER_TEXT_MODEL,
            system_prompt=TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT,
            messages=messages,
            response_format=self._response_format(MEAL_ESTIMATE_RESPONSE_SCHEMA, "meal_estimate"),
        )

    async def stream_meal_from_image(
        self,
        image_url: str,
        user_context: UserContext | None = None,
        optional_hint: str | None = None,
        additional_context: str | None = None,
    ) -> "AsyncIterator[dict]":
        data_uri = await self._load_image_data_uri(image_url)
        messages = self._build_image_messages(data_uri, user_context, optional_hint, additional_context)
        async for ev in self.stream_chat(
            model=settings.OPENROUTER_IMAGE_MODEL,
            system_prompt=IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT,
            messages=messages,
            response_format=self._response_format(MEAL_ESTIMATE_RESPONSE_SCHEMA, "meal_estimate"),
        ):
            yield ev

    # ---- response normalization (C25, shared by all estimate paths) ---------

    @staticmethod
    def _extract_json(raw_text: str) -> str:
        """Strip markdown fences / surrounding prose before json.loads (C8)."""
        if not raw_text:
            return raw_text
        text = raw_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return text

    def _parse_payload(self, raw_text: str, schema_type: type) -> tuple[dict[str, Any], bool]:
        """Returns (parsed_dict, degraded). Deterministic recovery runs BEFORE any
        paid repair call (C8). Raises JSONDecodeError only when nothing is salvageable."""
        try:
            return json.loads(self._extract_json(raw_text)), False
        except json.JSONDecodeError:
            recovered = self._recover_partial_json(raw_text, schema_type)
            if recovered is not None:
                logger.warning("Recovered partial estimate from malformed JSON response.")
                return recovered, True
            raise

    @staticmethod
    def _coerce_confidence(value: Any) -> str:
        if isinstance(value, str) and value.lower() in {"low", "medium", "high"}:
            return value.lower()
        return "medium"

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _dict_to_items(cls, parsed: dict[str, Any]) -> list[MealEstimateItem]:
        items = []
        for item in parsed.get("items") or []:
            if not isinstance(item, dict):
                continue
            items.append(
                MealEstimateItem(
                    name=item.get("name", ""),
                    quantity_estimate=item.get("quantity_estimate"),
                    weight_grams=cls._as_int(item.get("weight_grams")),
                    calories_per_100g=item.get("calories_per_100g"),
                    protein_g=item.get("protein_g"),
                    carbs_g=item.get("carbs_g"),
                    fat_g=item.get("fat_g"),
                    estimated_calories=cls._as_int(item.get("estimated_calories")) or 0,
                )
            )
        return items

    def _build_meal_estimate(
        self,
        parsed: dict[str, Any],
        *,
        source_type: str,
        model: str,
        prompt_version: str,
        raw_text: str,
        latency_ms: int,
        usage: dict | None,
        degraded: bool,
    ) -> MealEstimateResult:
        """Single dict->MealEstimateResult constructor shared by text/image/voice."""
        return MealEstimateResult(
            meal_name=parsed.get("meal_name", ""),
            estimated_calories=self._as_int(parsed.get("estimated_calories")) or 0,
            estimated_min_calories=self._as_int(parsed.get("estimated_min_calories")),
            estimated_max_calories=self._as_int(parsed.get("estimated_max_calories")),
            confidence=self._coerce_confidence(parsed.get("confidence")),
            meal_category_suggestion=parsed.get("meal_category_suggestion"),
            meal_category_confidence=parsed.get("meal_category_confidence"),
            source_type=source_type,
            items=self._dict_to_items(parsed),
            assumptions=parsed.get("assumptions") or [],
            needs_clarification=bool(parsed.get("needs_clarification", False)),
            clarifying_question=parsed.get("clarifying_question"),
            model_name=model,
            prompt_version=prompt_version,
            raw_output=raw_text,
            latency_ms=latency_ms,
            total_protein_g=parsed.get("total_protein_g"),
            total_carbs_g=parsed.get("total_carbs_g"),
            total_fat_g=parsed.get("total_fat_g"),
            estimation_reasoning=parsed.get("estimation_reasoning"),
            degraded_extraction=degraded,
            token_usage=self._normalize_usage(usage),
        )

    async def _parse_and_build_meal(
        self,
        raw_text: str,
        latency_ms: int,
        usage: dict | None,
        *,
        source_type: str,
        model: str,
        prompt_version: str,
    ) -> MealEstimateResult:
        try:
            parsed, degraded = self._parse_payload(raw_text, MealEstimateResult)
        except json.JSONDecodeError as e:
            parsed = await self._repair_json(raw_text, str(e), MealEstimateResult)
            degraded = True

        kwargs = {
            "source_type": source_type,
            "model": model,
            "prompt_version": prompt_version,
            "raw_text": raw_text,
            "latency_ms": latency_ms,
            "usage": usage,
        }
        try:
            return self._build_meal_estimate(parsed, degraded=degraded, **kwargs)
        except ValidationError as ve:
            parsed = await self._repair_json(raw_text, str(ve), MealEstimateResult)
            return self._build_meal_estimate(parsed, degraded=True, **kwargs)

    async def _repair_json(self, malformed_json: str, error_msg: str, schema_type: type) -> dict:
        """LAST-resort paid repair (C8): only reached after deterministic recovery fails."""
        logger.info("Attempting to repair JSON output using OpenRouter...")
        repair_messages = [
            {
                "role": "user",
                "content": f"Original malformed output: {malformed_json}\n\nValidation Error: {error_msg}",
            }
        ]
        try:
            repaired_text, _, _ = await self._post_openrouter(
                model=settings.OPENROUTER_TEXT_MODEL,
                system_prompt=JSON_REPAIR_SYSTEM_PROMPT,
                messages=repair_messages,
                response_format={"type": "json_object"},
            )
            try:
                return json.loads(self._extract_json(repaired_text))
            except json.JSONDecodeError:
                recovered = self._recover_partial_json(malformed_json, schema_type)
                if recovered is not None:
                    return recovered
                raise
        except Exception as e:
            recovered = self._recover_partial_json(malformed_json, schema_type)
            if recovered is not None:
                logger.warning("Recovered partial estimate after JSON repair failed.")
                return recovered
            logger.error(f"JSON repair failed: {e}")
            raise AIInvalidResponseError(
                f"Failed to repair JSON output. Original: {malformed_json}. Error: {error_msg}"
            )

    @staticmethod
    def _recover_partial_json(malformed_json: str, schema_type: type) -> dict[str, Any] | None:
        if schema_type is not MealEstimateResult:
            return None

        def last_int(key: str) -> int | None:
            matches = re.findall(rf'"{re.escape(key)}"\s*:\s*(-?\d+)', malformed_json)
            return int(matches[-1]) if matches else None

        def last_float(key: str) -> float | None:
            matches = re.findall(rf'"{re.escape(key)}"\s*:\s*(-?\d+(?:\.\d+)?)', malformed_json)
            return float(matches[-1]) if matches else None

        def first_string(key: str) -> str | None:
            match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', malformed_json)
            if not match:
                return None
            return json.loads(f'"{match.group(1)}"')

        estimated_calories = last_int("estimated_calories")
        meal_name = first_string("meal_name")
        if estimated_calories is None or not meal_name:
            return None

        confidence = first_string("confidence") or "low"
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"

        protein = last_float("total_protein_g")
        carbs = last_float("total_carbs_g")
        fat = last_float("total_fat_g")
        weight_grams = last_int("weight_grams")
        synthetic_item: dict[str, Any] = {
            "name": meal_name,
            "quantity_estimate": None,
            "weight_grams": weight_grams,
            "calories_per_100g": (
                round(estimated_calories / weight_grams * 100, 1) if weight_grams and weight_grams > 0 else None
            ),
            "protein_g": protein,
            "carbs_g": carbs,
            "fat_g": fat,
            "estimated_calories": estimated_calories,
        }

        return {
            "meal_name": meal_name,
            "estimated_calories": estimated_calories,
            "estimated_min_calories": last_int("estimated_min_calories"),
            "estimated_max_calories": last_int("estimated_max_calories"),
            "total_protein_g": protein,
            "total_carbs_g": carbs,
            "total_fat_g": fat,
            "confidence": confidence,
            "items": [synthetic_item],
            "assumptions": ["Recovered from a partial AI response."],
            "needs_clarification": False,
            "clarifying_question": None,
            "estimation_reasoning": None,
        }

    # ---- public estimation API ----------------------------------------------

    async def estimate_meal_from_text(
        self,
        input_text: str,
        user_context: UserContext | None = None,
        is_voice: bool = False,
        additional_context: str | None = None,
    ) -> MealEstimateResult:
        model = settings.OPENROUTER_TEXT_MODEL
        context_str = self._build_user_context(user_context)
        messages = [
            {
                "role": "user",
                "content": build_text_meal_estimation_user_prompt(
                    input_text,
                    context_str,
                    is_voice=is_voice,
                    additional_context=additional_context,
                ),
            }
        ]
        raw_text, latency_ms, usage = await self._post_openrouter(
            model=model,
            system_prompt=TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT,
            messages=messages,
            response_format=self._response_format(MEAL_ESTIMATE_RESPONSE_SCHEMA, "meal_estimate"),
        )
        return await self._parse_and_build_meal(
            raw_text,
            latency_ms,
            usage,
            source_type="text",
            model=model,
            prompt_version=TEXT_MEAL_ESTIMATION_PROMPT_VERSION,
        )

    def _prepare_image_data_uri(self, image_bytes: bytes, content_type: str) -> str:
        """C15: conservatively downscale a phone photo before base64 to cut image
        input tokens. Falls back to the original bytes on any failure."""
        if settings.AI_IMAGE_DOWNSCALE:
            try:
                from PIL import Image  # local import: optional dependency

                img = Image.open(BytesIO(image_bytes))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                max_edge = settings.AI_IMAGE_MAX_EDGE
                if max(img.size) > max_edge:
                    img.thumbnail((max_edge, max_edge))
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=settings.AI_IMAGE_JPEG_QUALITY, optimize=True)
                data = buf.getvalue()
                if len(data) < len(image_bytes):
                    b64 = base64.b64encode(data).decode("utf-8")
                    return f"data:image/jpeg;base64,{b64}"
            except Exception as e:
                logger.warning(f"Image downscale failed, using original bytes: {e}")
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        return f"data:{content_type};base64,{b64}"

    async def _load_image_data_uri(self, image_url: str) -> str:
        """Fetch/read a food image (remote URL or local path) and return a
        (downscaled) base64 data URI. Shared by the sync and streaming paths."""
        try:
            if not (image_url.startswith("http://") or image_url.startswith("https://")):
                clean_path = image_url.lstrip("/")
                local_path = clean_path if clean_path.startswith("app/") else f"app/{clean_path}"
                with open(local_path, "rb") as f:
                    image_content = f.read()
                if image_url.lower().endswith(".png"):
                    content_type = "image/png"
                elif image_url.lower().endswith(".gif"):
                    content_type = "image/gif"
                elif image_url.lower().endswith(".webp"):
                    content_type = "image/webp"
                else:
                    content_type = "image/jpeg"
            else:
                client = get_shared_client()
                image_res = await client.get(image_url, timeout=15.0)
                image_res.raise_for_status()
                content_type = image_res.headers.get("content-type", "image/jpeg").split(";", 1)[0].lower()
                image_content = image_res.content
            if not image_content or len(image_content) > settings.MEAL_UPLOAD_MAX_BYTES:
                raise ValueError("image is empty or exceeds the analysis size limit")
            if not content_type.startswith("image/"):
                raise ValueError(f"unsupported image content type: {content_type}")
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.warning("Image download returned HTTP %s", status_code)
            raise ImageAnalysisError(
                "The photo is no longer available. Please choose it again.",
                details={
                    "retryable": status_code in _RETRYABLE_STATUS_CODES,
                    "status_code": status_code,
                },
            )
        except httpx.RequestError as exc:
            logger.warning("Temporary image download failure: %s", exc)
            raise ImageAnalysisError(
                "The photo could not be downloaded. Please try again.",
                details={"retryable": True, "reason": type(exc).__name__},
            )
        except (OSError, ValueError) as exc:
            logger.warning("Unable to read image input: %s", exc)
            raise ImageAnalysisError(
                "The photo could not be read. Please choose another photo.",
                details={"retryable": False, "reason": type(exc).__name__},
            )

        return self._prepare_image_data_uri(image_content, content_type)

    def _build_image_messages(
        self,
        data_uri: str,
        user_context: UserContext | None,
        optional_hint: str | None,
        additional_context: str | None,
    ) -> list:
        context_str = self._build_user_context(user_context)
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": build_image_meal_estimation_user_text(
                            optional_hint,
                            context_str,
                            additional_context=additional_context,
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}},
                ],
            }
        ]

    def _build_text_messages(
        self,
        input_text: str,
        user_context: UserContext | None,
        is_voice: bool,
        additional_context: str | None,
    ) -> list:
        context_str = self._build_user_context(user_context)
        return [
            {
                "role": "user",
                "content": build_text_meal_estimation_user_prompt(
                    input_text,
                    context_str,
                    is_voice=is_voice,
                    additional_context=additional_context,
                ),
            }
        ]

    async def estimate_meal_from_image(
        self,
        image_url: str,
        user_context: UserContext | None = None,
        optional_hint: str | None = None,
        additional_context: str | None = None,
    ) -> MealEstimateResult:
        model = settings.OPENROUTER_IMAGE_MODEL

        data_uri = await self._load_image_data_uri(image_url)
        context_str = self._build_user_context(user_context)

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": build_image_meal_estimation_user_text(
                            optional_hint,
                            context_str,
                            additional_context=additional_context,
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}},
                ],
            }
        ]

        raw_text, latency_ms, usage = await self._post_openrouter(
            model=model,
            system_prompt=IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT,
            messages=messages,
            response_format=self._response_format(MEAL_ESTIMATE_RESPONSE_SCHEMA, "meal_estimate"),
        )
        return await self._parse_and_build_meal(
            raw_text,
            latency_ms,
            usage,
            source_type="photo",
            model=model,
            prompt_version=IMAGE_MEAL_ESTIMATION_PROMPT_VERSION,
        )

    async def refine_meal_estimate(
        self,
        meal_snapshot: dict,
        user_refinement: str,
        source_type: str,
        user_context: UserContext | None = None,
    ) -> MealEstimateResult:
        model = settings.OPENROUTER_TEXT_MODEL
        context_str = self._build_user_context(user_context)
        messages = [
            {
                "role": "user",
                "content": build_meal_refinement_user_prompt(
                    original_meal_json=json.dumps(meal_snapshot, ensure_ascii=False),
                    source_type=source_type,
                    user_refinement=user_refinement,
                    context=context_str,
                ),
            }
        ]
        raw_text, latency_ms, usage = await self._post_openrouter(
            model=model,
            system_prompt=MEAL_REFINEMENT_SYSTEM_PROMPT,
            messages=messages,
            response_format=self._response_format(MEAL_REFINEMENT_RESPONSE_SCHEMA, "meal_refinement"),
        )
        result = await self._parse_and_build_meal(
            raw_text,
            latency_ms,
            usage,
            source_type=source_type,
            model=model,
            prompt_version=MEAL_REFINEMENT_PROMPT_VERSION,
        )
        try:
            parsed, _ = self._parse_payload(raw_text, MealEstimateResult)
            result.ai_summary = parsed.get("ai_summary")
            result.changes_made = parsed.get("changes_made") or []
        except Exception:
            result.ai_summary = None
            result.changes_made = []
        return result

    async def transcribe_audio(self, audio_url: str) -> SpeechTranscriptionResult:
        model = settings.OPENROUTER_AUDIO_MODEL

        try:
            if not (audio_url.startswith("http://") or audio_url.startswith("https://")):
                clean_path = audio_url.lstrip("/")
                local_path = clean_path if clean_path.startswith("app/") else f"app/{clean_path}"
                with open(local_path, "rb") as f:
                    audio_content = f.read()
                if audio_url.lower().endswith(".m4a"):
                    content_type = "audio/m4a"
                elif audio_url.lower().endswith(".wav"):
                    content_type = "audio/wav"
                elif audio_url.lower().endswith(".ogg"):
                    content_type = "audio/ogg"
                else:
                    content_type = "audio/mp3"
            else:
                client = get_shared_client()
                audio_res = await client.get(audio_url, timeout=20.0)
                audio_res.raise_for_status()
                content_type = audio_res.headers.get("content-type", "audio/mp3").split(";", 1)[0].lower()
                if content_type == "application/octet-stream":
                    if audio_url.endswith(".m4a"):
                        content_type = "audio/m4a"
                    elif audio_url.endswith(".wav"):
                        content_type = "audio/wav"
                    else:
                        content_type = "audio/mp3"
                audio_content = audio_res.content
            if not audio_content or len(audio_content) > settings.MEAL_UPLOAD_MAX_BYTES:
                raise ValueError("audio is empty or exceeds the analysis size limit")
            if not content_type.startswith("audio/"):
                raise ValueError(f"unsupported audio content type: {content_type}")
            audio_base64 = base64.b64encode(audio_content).decode("utf-8")
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.warning("Audio download returned HTTP %s", status_code)
            raise SpeechTranscriptionError(
                "The recording is no longer available. Please record it again.",
                details={
                    "retryable": status_code in _RETRYABLE_STATUS_CODES,
                    "status_code": status_code,
                },
            )
        except httpx.RequestError as exc:
            logger.warning("Temporary audio download failure: %s", exc)
            raise SpeechTranscriptionError(
                "The recording could not be downloaded. Please try again.",
                details={"retryable": True, "reason": type(exc).__name__},
            )
        except (OSError, ValueError) as exc:
            logger.warning("Unable to read audio input: %s", exc)
            raise SpeechTranscriptionError(
                "The recording could not be read. Please record it again.",
                details={"retryable": False, "reason": type(exc).__name__},
            )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe the attached audio description of a meal verbatim."},
                    # OpenRouter uses the standard content-block structure for multimodal files.
                    {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{audio_base64}"}},
                ],
            }
        ]

        raw_text, latency_ms, usage = await self._post_openrouter(
            model=model,
            system_prompt=VOICE_TRANSCRIPTION_SYSTEM_PROMPT,
            messages=messages,
            response_format={"type": "json_object"},
        )

        # C23: salvage the transcript instead of failing the whole voice flow.
        transcript, confidence, language = "", "low", None
        try:
            parsed = json.loads(self._extract_json(raw_text))
            transcript = (parsed.get("transcript") or "").strip()
            confidence = self._coerce_confidence(parsed.get("confidence"))
            language = parsed.get("language")
        except (json.JSONDecodeError, AttributeError):
            logger.warning("Transcription JSON parse failed; salvaging raw text as transcript.")
        if not transcript:
            transcript = self._extract_json(raw_text).strip().strip("{}").strip() or raw_text.strip()
            confidence = "low"

        return SpeechTranscriptionResult(
            transcript=transcript,
            confidence=confidence,
            language=language,
            model_name=model,
            raw_output=raw_text,
            latency_ms=latency_ms,
            token_usage=self._normalize_usage(usage),
        )

    async def suggest_meal_completion(
        self,
        completion_req: MealCompletionRequest,
        user_context: UserContext | None = None,
    ) -> MealCompletionResult:
        model = settings.OPENROUTER_TEXT_MODEL
        context_str = self._build_user_context(user_context, leading_blank=True)

        req_info = (
            f"Remaining Calories: {completion_req.remaining_calories} kcal\n"
            f"Consumed Calories: {completion_req.consumed_calories} kcal\n"
            f"Daily Calorie Goal: {completion_req.daily_goal} kcal\n"
            f"Consumed Protein: {completion_req.consumed_protein_g}g\n"
            f"Consumed Carbs: {completion_req.consumed_carbs_g}g\n"
            f"Consumed Fat: {completion_req.consumed_fat_g}g\n"
            f"Target Protein: {f'{completion_req.target_protein_g}g' if completion_req.target_protein_g is not None else 'not set'}\n"
            f"Target Carbs: {f'{completion_req.target_carbs_g}g' if completion_req.target_carbs_g is not None else 'not set'}\n"
            f"Target Fat: {f'{completion_req.target_fat_g}g' if completion_req.target_fat_g is not None else 'not set'}\n"
            f"Meals Eaten Today: {', '.join(completion_req.meals_eaten_today) if completion_req.meals_eaten_today else 'None'}"
        )

        output_lang = "English"
        if user_context and user_context.locale:
            primary = user_context.locale.split("-")[0].split("_")[0].strip().lower()
            output_lang = _LANGUAGE_NAMES.get(primary, "English")

        messages = [
            {
                "role": "user",
                "content": (
                    f"Suggest 3 standalone meal alternatives based on this info:\n{req_info}{context_str}\n\n"
                    f"Write all free-text fields (meal_name, description, ingredients, preparation_hint, reasoning, "
                    f"daily_context_summary, macro_balance_note) in this language: {output_lang}. "
                    f"But keep meal_type and difficulty as the exact lowercase English enum values "
                    f"(lunch/dinner/snack, easy/medium)."
                ),
            }
        ]

        raw_text, latency_ms, usage = await self._post_openrouter(
            model=model,
            system_prompt=MEAL_COMPLETION_SYSTEM_PROMPT,
            messages=messages,
            response_format={"type": "json_object"},
        )

        try:
            parsed = json.loads(self._extract_json(raw_text))
        except json.JSONDecodeError as e:
            parsed = await self._repair_json(raw_text, str(e), MealCompletionResult)

        return self._build_completion_result(parsed, model, raw_text, latency_ms, usage)

    @staticmethod
    def _coerce_enum(value: Any, allowed: set[str], default: str) -> str:
        if isinstance(value, str) and value.lower() in allowed:
            return value.lower()
        return default

    def _build_completion_result(
        self, parsed: dict, model: str, raw_text: str, latency_ms: int, usage: dict | None
    ) -> MealCompletionResult:
        suggestions = [
            MealSuggestionItem(
                meal_name=item.get("meal_name", ""),
                description=item.get("description", ""),
                estimated_calories=self._as_int(item.get("estimated_calories")) or 0,
                protein_g=item.get("protein_g", 0.0) or 0.0,
                carbs_g=item.get("carbs_g", 0.0) or 0.0,
                fat_g=item.get("fat_g", 0.0) or 0.0,
                ingredients=item.get("ingredients", []) or [],
                preparation_hint=item.get("preparation_hint", ""),
                reasoning=item.get("reasoning", ""),
                meal_type=self._coerce_enum(item.get("meal_type"), {"lunch", "dinner", "snack"}, "snack"),
                difficulty=self._coerce_enum(item.get("difficulty"), {"easy", "medium"}, "easy"),
                prep_time_minutes=self._as_int(item.get("prep_time_minutes")) or 0,
            )
            for item in (parsed.get("suggestions", []) or [])
            if isinstance(item, dict)
        ]
        return MealCompletionResult(
            suggestions=suggestions,
            daily_context_summary=parsed.get("daily_context_summary", ""),
            macro_balance_note=parsed.get("macro_balance_note", ""),
            model_name=model,
            prompt_version=MEAL_COMPLETION_PROMPT_VERSION,
            raw_output=raw_text,
            latency_ms=latency_ms,
            token_usage=self._normalize_usage(usage),
        )

    async def generate_weekly_observation(
        self,
        pattern,
        *,
        days_analyzed: int = 7,
        locale: str = "en",
    ):
        from app.ai.prompts.insights import WEEKLY_OBSERVATION_SYSTEM_PROMPT
        from app.schemas.insights import WeeklyObservation

        if pattern is None:
            return None
        if settings.is_testing:
            return self._fallback_weekly_observation(pattern, days_analyzed=days_analyzed, locale=locale)
        verified = pattern.verified_dict()
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "output_language": self._insight_language(locale),
                        "verified_patterns": [verified],
                    },
                    separators=(",", ":"),
                ),
            }
        ]
        try:
            text, _, _ = await self._post_openrouter(
                model=settings.OPENROUTER_TEXT_MODEL,
                system_prompt=WEEKLY_OBSERVATION_SYSTEM_PROMPT,
                messages=messages,
                response_format={"type": "json_object"},
            )
            parsed = WeeklyObservation.model_validate(json.loads(self._extract_json(text)))
            return parsed.model_copy(
                update={
                    "confidence": self._insight_confidence(pattern.confidence),
                    "category": pattern.category,
                    "metric": self._insight_metric(pattern, locale=locale),
                    "days_analyzed": days_analyzed,
                    "evidence": self._insight_evidence(pattern, locale=locale),
                }
            )
        except Exception:
            return self._fallback_weekly_observation(pattern, days_analyzed=days_analyzed, locale=locale)

    async def generate_pattern_insights(
        self,
        patterns,
        *,
        locale: str = "en",
    ):
        from app.ai.prompts.insights import PATTERN_INSIGHTS_SYSTEM_PROMPT
        from app.schemas.insights import InsightCard

        if not patterns:
            return []
        if settings.is_testing:
            return [self._fallback_insight(pattern, locale=locale) for pattern in patterns[:4]]
        verified = [pattern.verified_dict() for pattern in patterns[:4]]
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "output_language": self._insight_language(locale),
                        "verified_patterns": verified,
                    },
                    separators=(",", ":"),
                ),
            }
        ]
        try:
            text, _, _ = await self._post_openrouter(
                model=settings.OPENROUTER_TEXT_MODEL,
                system_prompt=PATTERN_INSIGHTS_SYSTEM_PROMPT,
                messages=messages,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(self._extract_json(text))
            raw_insights = parsed.get("patterns", [])
            if not isinstance(raw_insights, list):
                return []
            insights = []
            for index, raw in enumerate(raw_insights[: len(patterns[:4])]):
                source = patterns[index]
                card = InsightCard.model_validate(raw)
                insights.append(
                    card.model_copy(
                        update={
                            "confidence": self._insight_confidence(source.confidence),
                            "category": source.category,
                            "metric": self._insight_metric(source, locale=locale),
                            "evidence": self._insight_evidence(source, locale=locale),
                        }
                    )
                )
            return insights
        except Exception:
            return [self._fallback_insight(pattern, locale=locale) for pattern in patterns[:4]]

    async def verbalize_insight_stories(self, pattern_inputs: list[dict], *, locale: str = "en"):
        """Strict narrator boundary. Raises after one malformed-output repair attempt."""
        from app.ai.prompts.insights import STORY_VERBALIZATION_SYSTEM_PROMPT
        from app.schemas.insights import InsightStory

        if not pattern_inputs:
            return []
        verified_inputs = pattern_inputs[:4]
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "output_language": self._insight_language(locale),
                        "verified_patterns": verified_inputs,
                    },
                    separators=(",", ":"),
                ),
            }
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "calry_insight_stories",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["insights"],
                    "properties": {
                        "insights": {
                            "type": "array",
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "story_id",
                                    "detector_id",
                                    "pattern_key",
                                    "title",
                                    "message",
                                    "confidence_label",
                                    "metric",
                                    "explanation",
                                    "evidence",
                                    "category",
                                ],
                                "properties": {
                                    "story_id": {"type": "string"},
                                    "detector_id": {"type": "string"},
                                    "pattern_key": {"type": "string"},
                                    "title": {"type": "string"},
                                    "message": {"type": "string"},
                                    "confidence_label": {
                                        "type": "string",
                                        "enum": ["low", "medium", "high"],
                                    },
                                    "metric": {"type": "string"},
                                    "explanation": {"type": "string"},
                                    "evidence": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["label", "value"],
                                            "properties": {
                                                "label": {"type": "string"},
                                                "value": {"type": "string"},
                                            },
                                        },
                                    },
                                    "category": {
                                        "type": "string",
                                        "enum": [
                                            "accuracy",
                                            "consistency",
                                            "macros",
                                            "meals",
                                            "activity",
                                            "water",
                                            "progress",
                                        ],
                                    },
                                },
                            },
                        }
                    },
                },
            },
        }
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                text, latency_ms, usage = await self._post_openrouter(
                    model=settings.OPENROUTER_TEXT_MODEL,
                    system_prompt=STORY_VERBALIZATION_SYSTEM_PROMPT,
                    messages=messages,
                    response_format=response_format,
                )
                parsed = json.loads(self._extract_json(text))
                raw_insights = parsed.get("insights")
                if not isinstance(raw_insights, list) or len(raw_insights) != len(verified_inputs):
                    raise ValueError("Insight count does not match verified input.")
                output = []
                for index, raw in enumerate(raw_insights):
                    source = verified_inputs[index]
                    immutable_fields = (
                        "story_id",
                        "detector_id",
                        "pattern_key",
                        "confidence_label",
                        "metric",
                        "category",
                    )
                    if any(raw.get(key) != source[key] for key in immutable_fields):
                        raise ValueError("An immutable verified field changed.")
                    raw_evidence = raw.get("evidence")
                    source_evidence = source["evidence"]
                    if (
                        not isinstance(raw_evidence, list)
                        or len(raw_evidence) != len(source_evidence)
                        or any(
                            not isinstance(item, dict) or item.get("value") != source_evidence[evidence_index]["value"]
                            for evidence_index, item in enumerate(raw_evidence)
                        )
                    ):
                        raise ValueError("Verified evidence values or order changed.")
                    repaired = {
                        **raw,
                        "story_id": source["story_id"],
                        "detector_id": source["detector_id"],
                        "pattern_key": source["pattern_key"],
                        "confidence_label": source["confidence_label"],
                        "metric": source["metric"],
                        "evidence": [
                            {"label": item["label"], "value": source_evidence[evidence_index]["value"]}
                            for evidence_index, item in enumerate(raw_evidence)
                        ],
                        "category": source["category"],
                        "direction": source["direction"],
                    }
                    output.append(InsightStory.model_validate(repaired))
                normalized_usage = self._normalize_usage(usage) or {}
                logger.info(
                    "event=llm_verbalization_completed model=%s story_count=%s attempt=%s "
                    "latency_ms=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s cost=%s",
                    settings.OPENROUTER_TEXT_MODEL,
                    len(output),
                    attempt + 1,
                    latency_ms,
                    normalized_usage.get("prompt_tokens"),
                    normalized_usage.get("completion_tokens"),
                    normalized_usage.get("total_tokens"),
                    usage.get("cost") if isinstance(usage, dict) else None,
                )
                return output
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    messages.append(
                        {
                            "role": "user",
                            "content": "Previous output was invalid. Return schema-valid JSON preserving every identifier, metric, category, evidence value, and input order exactly. Respect the input direction but do not output it.",
                        }
                    )
        raise ValueError("Invalid story verbalization output after repair.") from last_error

    @staticmethod
    def _insight_confidence(confidence: float) -> str:
        if confidence < 0.65:
            return "low"
        if confidence < 0.85:
            return "medium"
        return "high"

    @staticmethod
    def _insight_locale(locale: str) -> str:
        return (locale or "en").split(",", 1)[0].split("-", 1)[0].split("_", 1)[0].strip().lower() or "en"

    @classmethod
    def _insight_language(cls, locale: str) -> str:
        return _LANGUAGE_NAMES.get(cls._insight_locale(locale), "English")

    @classmethod
    def _insight_evidence(cls, pattern, *, locale: str = "en") -> list[str]:
        payload = pattern.payload
        italian = cls._insight_locale(locale) == "it"
        if pattern.id == "goal_consistency":
            evidence = [
                (
                    f"{payload['days_within_target']} giorni su {payload['days_logged']} erano nell’obiettivo"
                    if italian
                    else f"{payload['days_within_target']} of {payload['days_logged']} days were within target"
                )
            ]
            if "average_calories" in payload:
                evidence.append(
                    f"Media giornaliera: {payload['average_calories']} kcal"
                    if italian
                    else f"Daily average: {payload['average_calories']} kcal"
                )
            if "average_absolute_goal_difference" in payload:
                evidence.append(
                    f"Distanza media dall’obiettivo: {payload['average_absolute_goal_difference']} kcal"
                    if italian
                    else f"Average distance from goal: {payload['average_absolute_goal_difference']} kcal"
                )
            return evidence
        if pattern.id == "weekend_difference":
            return [
                (
                    f"Media weekend: {payload['weekend_average_calories']} kcal"
                    if italian
                    else f"Weekend average: {payload['weekend_average_calories']} kcal"
                ),
                (
                    f"Media feriale: {payload['weekday_average_calories']} kcal"
                    if italian
                    else f"Weekday average: {payload['weekday_average_calories']} kcal"
                ),
                (
                    f"Confronto basato su {payload['weekend_days']} giorni del weekend e {payload['weekday_days']} feriali"
                    if italian
                    else f"Based on {payload['weekend_days']} weekend and {payload['weekday_days']} weekday logs"
                ),
            ]
        if pattern.id == "meal_distribution":
            return [
                (
                    f"{payload['total_entries']} voci registrate in {payload['active_logging_days']} giorni attivi"
                    if italian
                    else f"{payload['total_entries']} entries logged across {payload['active_logging_days']} active days"
                ),
                (
                    f"Categoria registrata più spesso: {payload['most_logged_category']}"
                    if italian
                    else f"Most logged category: {payload['most_logged_category']}"
                ),
                (
                    f"Media: {payload['average_entries_per_active_day']:.1f} voci per giorno attivo"
                    if italian
                    else f"Average: {payload['average_entries_per_active_day']:.1f} entries per active day"
                ),
            ]
        if pattern.id == "macro_balance":
            largest_gap = payload["largest_target_gap"]
            target_ratio = payload[f"{largest_gap}_target_ratio"]
            macro_label = (
                {"protein": "Proteine", "carbs": "Carboidrati", "fat": "Grassi"}.get(largest_gap, largest_gap)
                if italian
                else {"protein": "Protein", "carbs": "Carbohydrates", "fat": "Fat"}.get(largest_gap, largest_gap)
            )
            return [
                (
                    f"{macro_label} rispetto all’obiettivo: {cls._percent(target_ratio)}%"
                    if italian
                    else f"{macro_label} relative to target: {cls._percent(target_ratio)}%"
                ),
                (
                    f"Proteine medie: {payload['average_protein_g']:.1f} g"
                    if italian
                    else f"Average protein: {payload['average_protein_g']:.1f} g"
                ),
                (
                    f"Carboidrati medi: {payload['average_carbs_g']:.1f} g"
                    if italian
                    else f"Average carbohydrates: {payload['average_carbs_g']:.1f} g"
                ),
                (
                    f"Grassi medi: {payload['average_fat_g']:.1f} g"
                    if italian
                    else f"Average fat: {payload['average_fat_g']:.1f} g"
                ),
                (
                    f"{payload['meals_with_macro_data']} pasti con dati sui macronutrienti"
                    if italian
                    else f"{payload['meals_with_macro_data']} meals included macro data"
                ),
            ]
        if pattern.id == "hydration_consistency":
            return [
                (
                    f"Acqua registrata per {payload['days_with_water_logs']} giorni"
                    if italian
                    else f"Water was logged on {payload['days_with_water_logs']} days"
                ),
                (
                    f"Intervallo: {payload['minimum_glasses']}–{payload['maximum_glasses']} bicchieri"
                    if italian
                    else f"Range: {payload['minimum_glasses']}–{payload['maximum_glasses']} glasses"
                ),
                (
                    f"Media recente: {payload['recent_average_glasses']:.1f} bicchieri"
                    if italian
                    else f"Recent average: {payload['recent_average_glasses']:.1f} glasses"
                ),
            ]
        if pattern.id == "activity_frequency":
            return [
                (
                    f"Attività registrata in {payload['active_days']} giorni su {payload['days_observed']}"
                    if italian
                    else f"Activity logged on {payload['active_days']} of {payload['days_observed']} days"
                ),
                (
                    f"Media nei giorni attivi: {payload['average_burned_calories_on_active_days']} kcal"
                    if italian
                    else f"Active-day average: {payload['average_burned_calories_on_active_days']} kcal"
                ),
                (
                    f"Totale attività: {payload['total_burned_calories']} kcal"
                    if italian
                    else f"Total activity: {payload['total_burned_calories']} kcal"
                ),
            ]
        if pattern.id == "logging_consistency":
            return [
                (
                    f"Registrazioni in {payload['days_logged']} giorni su {payload['period_days']}"
                    if italian
                    else f"Logged on {payload['days_logged']} of {payload['period_days']} days"
                ),
                (
                    f"Serie più lunga: {payload['longest_streak']} giorni"
                    if italian
                    else f"Longest streak: {payload['longest_streak']} days"
                ),
                (
                    f"{payload['total_meals']} pasti registrati"
                    if italian
                    else f"{payload['total_meals']} meals logged"
                ),
            ]
        if pattern.id == "ai_estimation_accuracy":
            sources = payload.get("correction_source_counts", {})
            main_source = max(sources, key=sources.get) if sources else None
            source_label = {"photo": "foto", "text": "testo", "voice": "voce"}.get(main_source, main_source)
            evidence = [
                (
                    f"{payload['confirmed_meals']} pasti confermati analizzati"
                    if italian
                    else f"{payload['confirmed_meals']} confirmed meals reviewed"
                ),
                (
                    f"{payload['estimates_within_ten_percent']} stime entro il 10%"
                    if italian
                    else f"{payload['estimates_within_ten_percent']} estimates were within 10%"
                ),
                (
                    f"Correzione media assoluta: {cls._decimal(payload['average_absolute_correction_percent'], locale=locale)}%"
                    if italian
                    else f"Average absolute correction: {payload['average_absolute_correction_percent']:.1f}%"
                ),
            ]
            if source_label:
                evidence.append(
                    f"Fonte più confermata: {source_label}" if italian else f"Most confirmed source: {main_source}"
                )
            return evidence
        if pattern.id == "ai_accuracy_trend":
            return [
                (
                    f"Correzione media precedente: {payload['older_average_absolute_correction_percent']:.1f}%"
                    if italian
                    else f"Earlier average correction: {payload['older_average_absolute_correction_percent']:.1f}%"
                ),
                (
                    f"Correzione media recente: {payload['recent_average_absolute_correction_percent']:.1f}%"
                    if italian
                    else f"Recent average correction: {payload['recent_average_absolute_correction_percent']:.1f}%"
                ),
                (
                    f"Confronto tra {payload['older_confirmed_meals']} e {payload['recent_confirmed_meals']} pasti confermati"
                    if italian
                    else f"Compared {payload['older_confirmed_meals']} earlier with {payload['recent_confirmed_meals']} recent confirmations"
                ),
            ]
        if pattern.id == "calories_trend":
            return [
                (
                    f"Media precedente: {payload['earlier_average_calories']} kcal"
                    if italian
                    else f"Earlier average: {payload['earlier_average_calories']} kcal"
                ),
                (
                    f"Media recente: {payload['recent_average_calories']} kcal"
                    if italian
                    else f"Recent average: {payload['recent_average_calories']} kcal"
                ),
                (
                    f"Confronto basato su {payload['earlier_days'] + payload['recent_days']} giorni"
                    if italian
                    else f"Based on {payload['earlier_days'] + payload['recent_days']} logged days"
                ),
            ]
        if pattern.id == "goal_adherence_change":
            return [
                (
                    f"Aderenza precedente: {cls._percent(payload['earlier_adherence_rate'])}%"
                    if italian
                    else f"Earlier adherence: {cls._percent(payload['earlier_adherence_rate'])}%"
                ),
                (
                    f"Aderenza recente: {cls._percent(payload['recent_adherence_rate'])}%"
                    if italian
                    else f"Recent adherence: {cls._percent(payload['recent_adherence_rate'])}%"
                ),
                (
                    f"Confronto basato su {payload['earlier_days'] + payload['recent_days']} giorni"
                    if italian
                    else f"Based on {payload['earlier_days'] + payload['recent_days']} logged days"
                ),
            ]
        return []

    @staticmethod
    def _percent(value: float) -> int:
        return round(value * 100)

    @classmethod
    def _decimal(cls, value: float, *, locale: str) -> str:
        rendered = f"{value:.1f}"
        return rendered.replace(".", ",") if cls._insight_locale(locale) == "it" else rendered

    @classmethod
    def _signed_decimal(cls, value: float, *, locale: str) -> str:
        rendered = f"{value:+.1f}"
        return rendered.replace(".", ",") if cls._insight_locale(locale) == "it" else rendered

    @classmethod
    def _insight_metric(cls, pattern, *, locale: str = "en") -> str:
        payload = pattern.payload
        italian = cls._insight_locale(locale) == "it"
        accepted_rate = float(payload.get("accepted_without_changes_rate", 0))
        largest_macro_gap = payload.get("largest_target_gap", "protein")
        macro_labels = (
            {"protein": "Proteine", "carbs": "Carboidrati", "fat": "Grassi"}
            if italian
            else {"protein": "Protein", "carbs": "Carbohydrates", "fat": "Fat"}
        )
        macro_target_metric = (
            f"{macro_labels.get(largest_macro_gap, largest_macro_gap)}: "
            f"{cls._percent(payload.get(f'{largest_macro_gap}_target_ratio', 0))}% "
            f"{'dell’obiettivo' if italian else 'of target'}"
        )
        if accepted_rate >= 0.5:
            accuracy_metric = (
                f"{cls._percent(accepted_rate)}% confermate senza modifiche"
                if italian
                else f"{cls._percent(accepted_rate)}% accepted unchanged"
            )
        else:
            within_ten_rate = float(payload.get("accuracy_rate_within_ten_percent", 0))
            accuracy_metric = (
                f"{cls._percent(within_ten_rate)}% entro ±10%"
                if italian
                else f"{cls._percent(within_ten_rate)}% within ±10%"
            )
        metrics = (
            {
                "goal_consistency": lambda: f"{cls._percent(payload['adherence_rate'])}% dei giorni nell’obiettivo",
                "weekend_difference": lambda: f"{payload['difference_calories']:+d} kcal nel weekend",
                "meal_distribution": lambda: f"{payload['total_entries']} voci in {payload['active_logging_days']} giorni attivi",
                "macro_balance": lambda: macro_target_metric,
                "hydration_consistency": lambda: f"{payload['average_glasses']:.1f} bicchieri al giorno",
                "activity_frequency": lambda: f"{payload['active_days']} giorni attivi",
                "logging_consistency": lambda: f"{payload['longest_streak']} giorni consecutivi",
                "ai_estimation_accuracy": lambda: accuracy_metric,
                "ai_accuracy_trend": lambda: f"{cls._signed_decimal(payload['absolute_percentage_point_change'], locale=locale)} punti percentuali",
                "calories_trend": lambda: f"{payload['change_calories']:+d} kcal",
                "goal_adherence_change": lambda: f"{cls._percent(payload['adherence_rate_change']):+d} punti percentuali",
            }
            if italian
            else {
                "goal_consistency": lambda: f"{cls._percent(payload['adherence_rate'])}% within target",
                "weekend_difference": lambda: f"{payload['difference_calories']:+d} kcal on weekends",
                "meal_distribution": lambda: f"{payload['total_entries']} entries across {payload['active_logging_days']} active days",
                "macro_balance": lambda: macro_target_metric,
                "hydration_consistency": lambda: f"{payload['average_glasses']:.1f} glasses per logged day",
                "activity_frequency": lambda: f"{payload['active_days']} active days",
                "logging_consistency": lambda: f"{payload['longest_streak']}-day logging streak",
                "ai_estimation_accuracy": lambda: accuracy_metric,
                "ai_accuracy_trend": lambda: f"{payload['absolute_percentage_point_change']:+.1f} percentage points",
                "calories_trend": lambda: f"{payload['change_calories']:+d} kcal",
                "goal_adherence_change": lambda: f"{cls._percent(payload['adherence_rate_change']):+d} percentage points",
            }
        )
        builder = metrics.get(pattern.id)
        return builder() if builder else pattern.id.replace("_", " ")

    @classmethod
    def _fallback_insight(cls, pattern, *, locale: str = "en"):
        from app.schemas.insights import InsightCard

        payload = pattern.payload
        italian = cls._insight_locale(locale) == "it"
        if pattern.id == "goal_consistency":
            title, message = (
                ("Costanza rispetto all’obiettivo" if italian else "Goal consistency"),
                (
                    f"Hai registrato {payload['days_within_target']} giorni su {payload['days_logged']} entro il tuo obiettivo."
                    if italian
                    else f"You logged {payload['days_within_target']} of {payload['days_logged']} days within your target."
                ),
            )
        elif pattern.id == "weekend_difference":
            title, message = (
                ("La differenza del weekend" if italian else "Weekend difference"),
                (
                    f"Nel weekend la media è stata {payload['weekend_average_calories']} kcal, contro {payload['weekday_average_calories']} kcal nei giorni feriali."
                    if italian
                    else f"Weekend intake averaged {payload['weekend_average_calories']} kcal versus {payload['weekday_average_calories']} kcal on weekdays."
                ),
            )
        elif pattern.id == "meal_distribution":
            title, message = (
                ("Ritmo dei pasti registrati" if italian else "Meal logging pattern"),
                (
                    f"Hai registrato {payload['total_entries']} voci in {payload['active_logging_days']} giorni attivi."
                    if italian
                    else f"You logged {payload['total_entries']} entries across {payload['active_logging_days']} active days."
                ),
            )
        elif pattern.id == "macro_balance":
            title, message = (
                ("Equilibrio dei macronutrienti" if italian else "Macro balance"),
                (
                    f"Nei giorni registrati hai avuto in media {payload['average_protein_g']:.1f} g di proteine, {payload['average_carbs_g']:.1f} g di carboidrati e {payload['average_fat_g']:.1f} g di grassi."
                    if italian
                    else f"Logged days averaged {payload['average_protein_g']:.1f} g protein, {payload['average_carbs_g']:.1f} g carbs, and {payload['average_fat_g']:.1f} g fat."
                ),
            )
        elif pattern.id == "hydration_consistency":
            title, message = (
                ("Ritmo dell’idratazione" if italian else "Hydration pattern"),
                (
                    f"Hai registrato in media {payload['average_glasses']:.1f} bicchieri in {payload['days_with_water_logs']} giorni."
                    if italian
                    else f"Water logs averaged {payload['average_glasses']:.1f} glasses across {payload['days_with_water_logs']} days."
                ),
            )
        elif pattern.id == "activity_frequency":
            title, message = (
                ("Ritmo dell’attività" if italian else "Activity pattern"),
                (
                    f"Hai registrato attività in {payload['active_days']} giorni su {payload['days_observed']}."
                    if italian
                    else f"Activity was logged on {payload['active_days']} of {payload['days_observed']} observed days."
                ),
            )
        elif pattern.id == "logging_consistency":
            title, message = (
                ("Costanza nella registrazione" if italian else "Logging consistency"),
                (
                    f"Hai registrato {payload['days_logged']} giorni su {payload['period_days']}, con una serie massima di {payload['longest_streak']} giorni."
                    if italian
                    else f"You logged {payload['days_logged']} of {payload['period_days']} days, with a {payload['longest_streak']}-day longest streak."
                ),
            )
        elif pattern.id == "ai_estimation_accuracy":
            title, message = (
                ("Precisione delle stime" if italian else "Estimation accuracy"),
                (
                    f"Il {cls._percent(payload['accuracy_rate_within_ten_percent'])}% delle stime era entro ±10% su {payload['confirmed_meals']} pasti confermati."
                    if italian
                    else f"{cls._percent(payload['accuracy_rate_within_ten_percent'])}% of estimates were within ±10% across {payload['confirmed_meals']} confirmed meals."
                ),
            )
        elif pattern.id == "ai_accuracy_trend":
            worsened = payload.get("direction") == "worsened"
            title, message = (
                (
                    "Le stime recenti richiedono più correzioni"
                    if italian and worsened
                    else "Le stime recenti sono migliorate"
                    if italian
                    else "Recent estimates needed more correction"
                    if worsened
                    else "Recent estimates improved"
                ),
                (
                    f"La correzione media è passata dal {payload['older_average_absolute_correction_percent']:.1f}% al {payload['recent_average_absolute_correction_percent']:.1f}%."
                    if italian
                    else f"Average correction changed from {payload['older_average_absolute_correction_percent']:.1f}% to {payload['recent_average_absolute_correction_percent']:.1f}%."
                ),
            )
        elif pattern.id == "calories_trend":
            title, message = (
                ("Andamento delle calorie" if italian else "Calorie trend"),
                (
                    f"La media è passata da {payload['earlier_average_calories']} a {payload['recent_average_calories']} kcal."
                    if italian
                    else f"Average intake changed from {payload['earlier_average_calories']} to {payload['recent_average_calories']} kcal."
                ),
            )
        elif pattern.id == "goal_adherence_change":
            title, message = (
                ("Aderenza all’obiettivo" if italian else "Goal adherence changed"),
                (
                    f"L’aderenza all’obiettivo è passata dal {cls._percent(payload['earlier_adherence_rate'])}% al {cls._percent(payload['recent_adherence_rate'])}%."
                    if italian
                    else f"Goal adherence changed from {cls._percent(payload['earlier_adherence_rate'])}% to {cls._percent(payload['recent_adherence_rate'])}%."
                ),
            )
        else:
            title, message = (
                ("Pattern verificato", cls._insight_metric(pattern, locale=locale))
                if italian
                else ("Verified pattern", cls._insight_metric(pattern, locale=locale))
            )
        return InsightCard(
            title=title,
            message=message,
            confidence=cls._insight_confidence(pattern.confidence),
            category=pattern.category,
            metric=cls._insight_metric(pattern, locale=locale),
            evidence=cls._insight_evidence(pattern, locale=locale),
        )

    @classmethod
    def _fallback_weekly_observation(cls, pattern, *, days_analyzed: int, locale: str = "en"):
        from app.schemas.insights import WeeklyObservation

        card = cls._fallback_insight(pattern, locale=locale)
        return WeeklyObservation(
            **card.model_dump(),
            days_analyzed=days_analyzed,
            explanation=(
                "Basato solo sulle metriche verificate mostrate qui."
                if cls._insight_locale(locale) == "it"
                else "Based only on the verified metrics shown here."
            ),
        )
