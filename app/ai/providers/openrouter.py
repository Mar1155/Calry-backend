import asyncio
import base64
import json
import logging
import re
import time
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
from app.ai.prompts.voice_transcription import (
    VOICE_TRANSCRIPTION_SYSTEM_PROMPT,
)
from app.ai.providers.base import BaseAIProvider
from app.ai.schemas.meal_completion import MealCompletionRequest, MealCompletionResult, MealSuggestionItem
from app.ai.schemas.meal_estimate import (
    MEAL_ESTIMATE_RESPONSE_SCHEMA,
    MealEstimateItem,
    MealEstimateResult,
    SpeechTranscriptionResult,
    UserContext,
)
from app.core.config import settings

logger = logging.getLogger("app.ai.openrouter")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

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
                "User Recent Calorie Correction Patterns:\n"
                f"{user_context.previous_corrections_summary}"
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
            return min(float(value), 5.0)
        except ValueError:
            return None

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
            "temperature": 0.1,
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

        for attempt in range(max_retries + 1):
            retry_after: float | None = None
            try:
                start_time = time.perf_counter()
                response = await client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=timeout)
                latency_ms = int((time.perf_counter() - start_time) * 1000)

                if response.status_code == 200:
                    res_json = response.json()
                    try:
                        text_out = res_json["choices"][0]["message"]["content"]
                    except (KeyError, IndexError):
                        logger.error(f"Malformed OpenRouter API structure: {res_json}")
                        raise AIProviderError("Invalid response format received from OpenRouter API.")
                    return text_out, latency_ms, res_json.get("usage")

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
                    f"OpenRouter API attempt {attempt + 1} failed: {response.status_code} - {response.text}"
                )
                if attempt == max_retries:
                    raise AIProviderError(f"OpenRouter API returned error: {response.status_code} - {response.text}")
                retry_after = self._retry_after_seconds(response)

            except httpx.RequestError as e:
                logger.warning(f"OpenRouter API request error on attempt {attempt + 1}: {e}")
                if attempt == max_retries:
                    raise AIProviderError(f"OpenRouter service communication failure: {str(e)}")

            if attempt < max_retries:
                await asyncio.sleep(retry_after if retry_after else 0.5)

        raise AIProviderError("OpenRouter API call failed after retries.")

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
        for item in (parsed.get("items") or []):
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
            "source_type": source_type, "model": model, "prompt_version": prompt_version,
            "raw_text": raw_text, "latency_ms": latency_ms, "usage": usage,
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
            "calories_per_100g": round(estimated_calories / weight_grams * 100, 1)
            if weight_grams and weight_grams > 0
            else None,
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
            raw_text, latency_ms, usage,
            source_type="text", model=model, prompt_version=TEXT_MEAL_ESTIMATION_PROMPT_VERSION,
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

    async def estimate_meal_from_image(
        self,
        image_url: str,
        user_context: UserContext | None = None,
        optional_hint: str | None = None,
        additional_context: str | None = None,
    ) -> MealEstimateResult:
        model = settings.OPENROUTER_IMAGE_MODEL

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
                content_type = image_res.headers.get("content-type", "image/jpeg")
                image_content = image_res.content
        except Exception as e:
            logger.error(f"Failed to download or load image from {image_url}: {e}")
            raise ImageAnalysisError(f"Could not retrieve food image for analysis: {str(e)}")

        data_uri = self._prepare_image_data_uri(image_content, content_type)
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
                    {"type": "image_url", "image_url": {"url": data_uri}},
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
            raw_text, latency_ms, usage,
            source_type="photo", model=model, prompt_version=IMAGE_MEAL_ESTIMATION_PROMPT_VERSION,
        )

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
                content_type = audio_res.headers.get("content-type", "audio/mp3")
                if content_type == "application/octet-stream":
                    if audio_url.endswith(".m4a"):
                        content_type = "audio/m4a"
                    elif audio_url.endswith(".wav"):
                        content_type = "audio/wav"
                    else:
                        content_type = "audio/mp3"
                audio_content = audio_res.content
            audio_base64 = base64.b64encode(audio_content).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to download or load audio from {audio_url}: {e}")
            raise SpeechTranscriptionError(f"Could not retrieve spoken audio for transcription: {str(e)}")

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
            f"Meals Eaten Today: {', '.join(completion_req.meals_eaten_today) if completion_req.meals_eaten_today else 'None'}"
        )

        lang_map = {"it": "Italian", "es": "Spanish", "zh": "Chinese", "ja": "Japanese", "ar": "Arabic", "en": "English"}
        output_lang = "English"
        if user_context and user_context.locale:
            primary = user_context.locale.split("-")[0].split("_")[0].strip().lower()
            output_lang = lang_map.get(primary, "English")

        messages = [
            {
                "role": "user",
                "content": (
                    f"Suggest 3 meals/recipes to complete my day based on this info:\n{req_info}{context_str}\n\n"
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
        avg_calories: int,
        days_in_target: int,
        total_days: int,
        highest: int,
        lowest: int,
        most_frequent_meal: str | None,
        goal: int,
    ) -> str:
        from app.ai.prompts.insights import WEEKLY_OBSERVATION_SYSTEM_PROMPT

        data_summary = (
            f"Weekly stats:\n"
            f"- Average calories: {avg_calories} kcal (goal: {goal} kcal)\n"
            f"- Days within target: {days_in_target}/{total_days}\n"
            f"- Highest day: {highest} kcal, Lowest day: {lowest} kcal\n"
            f"- Most logged meal: {most_frequent_meal or 'unknown'}\n"
            f"- Variance: {highest - lowest} kcal between best and worst day"
        )
        messages = [{"role": "user", "content": data_summary}]
        try:
            text, _, _ = await self._post_openrouter(
                model=settings.OPENROUTER_TEXT_MODEL,
                system_prompt=WEEKLY_OBSERVATION_SYSTEM_PROMPT,
                messages=messages,
            )
            return text.strip()
        except Exception:
            return "Keep logging consistently to unlock personalized AI observations."

    async def generate_pattern_insights(
        self,
        correction_summary: str | None,
        avg_correction_pct: float | None,
        days_logged: int,
        days_in_target: int,
        avg_calories: int,
        goal: int,
    ) -> list[str]:
        from app.ai.prompts.insights import PATTERN_INSIGHTS_SYSTEM_PROMPT

        data_summary = (
            f"User data:\n"
            f"- Days logged in past 30 days: {days_logged}\n"
            f"- Days within calorie target: {days_in_target}\n"
            f"- Average daily calories: {avg_calories} kcal (goal: {goal} kcal)\n"
        )
        if correction_summary:
            data_summary += f"- AI correction history: {correction_summary}\n"
        if avg_correction_pct is not None:
            data_summary += f"- Average correction %: {avg_correction_pct:.1f}%\n"
        messages = [{"role": "user", "content": data_summary}]
        try:
            text, _, _ = await self._post_openrouter(
                model=settings.OPENROUTER_TEXT_MODEL,
                system_prompt=PATTERN_INSIGHTS_SYSTEM_PROMPT,
                messages=messages,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(self._extract_json(text))
            return parsed.get("patterns", [])
        except Exception:
            return ["Log more meals to unlock AI pattern insights."]
