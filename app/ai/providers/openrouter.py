import base64
import json
import logging
import time
import httpx
from pydantic import ValidationError

from app.ai.providers.base import BaseAIProvider
from app.ai.schemas.meal_estimate import MealEstimateResult, SpeechTranscriptionResult, UserContext, MealEstimateItem
from app.ai.schemas.meal_completion import MealCompletionResult, MealCompletionRequest, MealSuggestionItem
from app.ai.prompts.meal_estimation import (
    TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT,
    TEXT_MEAL_ESTIMATION_PROMPT_VERSION,
    JSON_REPAIR_SYSTEM_PROMPT,
)
from app.ai.prompts.meal_completion import (
    MEAL_COMPLETION_SYSTEM_PROMPT,
    MEAL_COMPLETION_PROMPT_VERSION,
)
from app.ai.prompts.image_estimation import (
    IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT,
    IMAGE_MEAL_ESTIMATION_PROMPT_VERSION,
)
from app.ai.prompts.voice_transcription import (
    VOICE_TRANSCRIPTION_SYSTEM_PROMPT,
    VOICE_TRANSCRIPTION_PROMPT_VERSION,
)
from app.ai.errors import (
    AIProviderError,
    AIInvalidResponseError,
    ImageAnalysisError,
    SpeechTranscriptionError,
)
from app.core.config import settings

logger = logging.getLogger("app.ai.openrouter")


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

    async def _post_openrouter(
        self,
        model: str,
        system_prompt: str,
        messages: list,
        response_format: dict | None = None,
    ) -> tuple[str, int]:
        """Performs raw POST request to the OpenRouter API."""
        api_key = self._get_api_key()
        url = "https://openrouter.ai/api/v1/chat/completions"
        
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        
        payload = {
            "model": model,
            "messages": full_messages,
            "temperature": 0.1,
        }
        if response_format:
            payload["response_format"] = response_format
            
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Calry",
        }
        
        timeout = float(settings.AI_REQUEST_TIMEOUT_SECONDS)
        max_retries = int(settings.AI_MAX_RETRIES)
        
        start_time = time.perf_counter()
        
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    latency_ms = int((time.perf_counter() - start_time) * 1000)
                    
                    if response.status_code == 200:
                        res_json = response.json()
                        try:
                            text_out = res_json["choices"][0]["message"]["content"]
                            return text_out, latency_ms
                        except (KeyError, IndexError) as e:
                            logger.error(f"Malformed OpenRouter API structure: {res_json}")
                            raise AIProviderError("Invalid response format received from OpenRouter API.")
                    
                    logger.warning(
                        f"OpenRouter API attempt {attempt + 1} failed: {response.status_code} - {response.text}"
                    )
                    if attempt == max_retries:
                        raise AIProviderError(f"OpenRouter API returned error: {response.status_code} - {response.text}")
                        
            except httpx.RequestError as e:
                logger.warning(f"OpenRouter API request error on attempt {attempt + 1}: {e}")
                if attempt == max_retries:
                    raise AIProviderError(f"OpenRouter service communication failure: {str(e)}")
            
            time.sleep(0.5)
            
        raise AIProviderError("OpenRouter API call failed after retries.")

    async def _repair_json(self, malformed_json: str, error_msg: str, schema_type: type) -> dict:
        """Attempts to repair invalid JSON by querying the model with a repair prompt."""
        logger.info("Attempting to repair JSON output using OpenRouter...")
        repair_messages = [
            {
                "role": "user",
                "content": f"Original malformed output: {malformed_json}\n\nValidation Error: {error_msg}"
            }
        ]
        
        try:
            repaired_text, _ = await self._post_openrouter(
                model=settings.OPENROUTER_TEXT_MODEL,
                system_prompt=JSON_REPAIR_SYSTEM_PROMPT,
                messages=repair_messages,
                response_format={"type": "json_object"},
            )
            return json.loads(repaired_text)
        except Exception as e:
            logger.error(f"JSON repair failed: {e}")
            raise AIInvalidResponseError(
                f"Failed to repair JSON output. Original: {malformed_json}. Error: {error_msg}"
            )

    async def estimate_meal_from_text(
        self, input_text: str, user_context: UserContext | None = None
    ) -> MealEstimateResult:
        model = settings.OPENROUTER_TEXT_MODEL
        
        context_str = ""
        if user_context:
            context_parts = []
            if user_context.sex or user_context.age or user_context.height_cm or user_context.weight_kg:
                profile = []
                if user_context.sex: profile.append(f"Sex: {user_context.sex}")
                if user_context.age: profile.append(f"Age: {user_context.age}")
                if user_context.height_cm: profile.append(f"Height: {user_context.height_cm} cm")
                if user_context.weight_kg: profile.append(f"Weight: {user_context.weight_kg} kg")
                context_parts.append(f"User Physical Profile: {', '.join(profile)}")
            if user_context.daily_calorie_goal:
                context_parts.append(f"User Daily Calorie Goal: {user_context.daily_calorie_goal} kcal")
            if user_context.previous_corrections_summary:
                context_parts.append(f"User Recent Calorie Correction Patterns:\n{user_context.previous_corrections_summary}")
            if context_parts:
                context_str = "\n\nUSER CONTEXT:\n" + "\n".join(context_parts)
            
        messages = [
            {
                "role": "user",
                "content": f"Estimate calories for this description: '{input_text}'{context_str}"
            }
        ]
        
        raw_text, latency_ms = await self._post_openrouter(
            model=model,
            system_prompt=TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT,
            messages=messages,
            response_format={"type": "json_object"},
        )
        
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            parsed = await self._repair_json(raw_text, str(e), MealEstimateResult)
            
        try:
            result = MealEstimateResult(
                meal_name=parsed.get("meal_name", ""),
                estimated_calories=parsed.get("estimated_calories", 0),
                estimated_min_calories=parsed.get("estimated_min_calories"),
                estimated_max_calories=parsed.get("estimated_max_calories"),
                confidence=parsed.get("confidence", "medium"),
                source_type="text",
                items=[
                    MealEstimateItem(
                        name=item.get("name", ""),
                        quantity_estimate=item.get("quantity_estimate"),
                        weight_grams=item.get("weight_grams"),
                        protein_g=item.get("protein_g"),
                        carbs_g=item.get("carbs_g"),
                        fat_g=item.get("fat_g"),
                        estimated_calories=item.get("estimated_calories", 0),
                    )
                    for item in parsed.get("items", [])
                ],
                assumptions=parsed.get("assumptions", []),
                needs_clarification=parsed.get("needs_clarification", False),
                clarifying_question=parsed.get("clarifying_question"),
                model_name=model,
                prompt_version=TEXT_MEAL_ESTIMATION_PROMPT_VERSION,
                raw_output=raw_text,
                latency_ms=latency_ms,
                total_protein_g=parsed.get("total_protein_g"),
                total_carbs_g=parsed.get("total_carbs_g"),
                total_fat_g=parsed.get("total_fat_g"),
                estimation_reasoning=parsed.get("estimation_reasoning"),
            )
            return result
        except ValidationError as ve:
            parsed = await self._repair_json(raw_text, str(ve), MealEstimateResult)
            return MealEstimateResult(
                meal_name=parsed.get("meal_name", ""),
                estimated_calories=parsed.get("estimated_calories", 0),
                estimated_min_calories=parsed.get("estimated_min_calories"),
                estimated_max_calories=parsed.get("estimated_max_calories"),
                confidence=parsed.get("confidence", "medium"),
                source_type="text",
                items=[
                    MealEstimateItem(
                        name=item.get("name", ""),
                        quantity_estimate=item.get("quantity_estimate"),
                        weight_grams=item.get("weight_grams"),
                        protein_g=item.get("protein_g"),
                        carbs_g=item.get("carbs_g"),
                        fat_g=item.get("fat_g"),
                        estimated_calories=item.get("estimated_calories", 0),
                    )
                    for item in parsed.get("items", [])
                ],
                assumptions=parsed.get("assumptions", []),
                needs_clarification=parsed.get("needs_clarification", False),
                clarifying_question=parsed.get("clarifying_question"),
                model_name=model,
                prompt_version=TEXT_MEAL_ESTIMATION_PROMPT_VERSION,
                raw_output=raw_text,
                latency_ms=latency_ms,
                total_protein_g=parsed.get("total_protein_g"),
                total_carbs_g=parsed.get("total_carbs_g"),
                total_fat_g=parsed.get("total_fat_g"),
                estimation_reasoning=parsed.get("estimation_reasoning"),
            )

    async def estimate_meal_from_image(
        self,
        image_url: str,
        user_context: UserContext | None = None,
        optional_hint: str | None = None,
    ) -> MealEstimateResult:
        model = settings.OPENROUTER_IMAGE_MODEL
        
        try:
            if not (image_url.startswith("http://") or image_url.startswith("https://")):
                clean_path = image_url.lstrip("/")
                if not clean_path.startswith("app/"):
                    local_path = f"app/{clean_path}"
                else:
                    local_path = clean_path
                
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
                
                image_base64 = base64.b64encode(image_content).decode("utf-8")
            else:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    image_res = await client.get(image_url)
                    image_res.raise_for_status()
                    content_type = image_res.headers.get("content-type", "image/jpeg")
                    image_base64 = base64.b64encode(image_res.content).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to download or load image from {image_url}: {e}")
            raise ImageAnalysisError(f"Could not retrieve food image for analysis: {str(e)}")
            
        context_str = ""
        if user_context:
            context_parts = []
            if user_context.sex or user_context.age or user_context.height_cm or user_context.weight_kg:
                profile = []
                if user_context.sex: profile.append(f"Sex: {user_context.sex}")
                if user_context.age: profile.append(f"Age: {user_context.age}")
                if user_context.height_cm: profile.append(f"Height: {user_context.height_cm} cm")
                if user_context.weight_kg: profile.append(f"Weight: {user_context.weight_kg} kg")
                context_parts.append(f"User Physical Profile: {', '.join(profile)}")
            if user_context.daily_calorie_goal:
                context_parts.append(f"User Daily Calorie Goal: {user_context.daily_calorie_goal} kcal")
            if user_context.previous_corrections_summary:
                context_parts.append(f"User Recent Calorie Correction Patterns:\n{user_context.previous_corrections_summary}")
            if context_parts:
                context_str = "\n\nUSER CONTEXT:\n" + "\n".join(context_parts)
            
        hint_str = f"\nUser hint/description: '{optional_hint}'" if optional_hint else ""
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Analyze this food photo.{hint_str}{context_str}"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{content_type};base64,{image_base64}"
                        }
                    }
                ]
            }
        ]
        
        raw_text, latency_ms = await self._post_openrouter(
            model=model,
            system_prompt=IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT,
            messages=messages,
            response_format={"type": "json_object"},
        )
        
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            parsed = await self._repair_json(raw_text, str(e), MealEstimateResult)
            
        try:
            result = MealEstimateResult(
                meal_name=parsed.get("meal_name", ""),
                estimated_calories=parsed.get("estimated_calories", 0),
                estimated_min_calories=parsed.get("estimated_min_calories"),
                estimated_max_calories=parsed.get("estimated_max_calories"),
                confidence=parsed.get("confidence", "medium"),
                source_type="photo",
                items=[
                    MealEstimateItem(
                        name=item.get("name", ""),
                        quantity_estimate=item.get("quantity_estimate"),
                        weight_grams=item.get("weight_grams"),
                        protein_g=item.get("protein_g"),
                        carbs_g=item.get("carbs_g"),
                        fat_g=item.get("fat_g"),
                        estimated_calories=item.get("estimated_calories", 0),
                    )
                    for item in parsed.get("items", [])
                ],
                assumptions=parsed.get("assumptions", []),
                needs_clarification=parsed.get("needs_clarification", False),
                clarifying_question=parsed.get("clarifying_question"),
                model_name=model,
                prompt_version=IMAGE_MEAL_ESTIMATION_PROMPT_VERSION,
                raw_output=raw_text,
                latency_ms=latency_ms,
                total_protein_g=parsed.get("total_protein_g"),
                total_carbs_g=parsed.get("total_carbs_g"),
                total_fat_g=parsed.get("total_fat_g"),
                estimation_reasoning=parsed.get("estimation_reasoning"),
            )
            return result
        except ValidationError as ve:
            parsed = await self._repair_json(raw_text, str(ve), MealEstimateResult)
            return MealEstimateResult(
                meal_name=parsed.get("meal_name", ""),
                estimated_calories=parsed.get("estimated_calories", 0),
                estimated_min_calories=parsed.get("estimated_min_calories"),
                estimated_max_calories=parsed.get("estimated_max_calories"),
                confidence=parsed.get("confidence", "medium"),
                source_type="photo",
                items=[
                    MealEstimateItem(
                        name=item.get("name", ""),
                        quantity_estimate=item.get("quantity_estimate"),
                        weight_grams=item.get("weight_grams"),
                        protein_g=item.get("protein_g"),
                        carbs_g=item.get("carbs_g"),
                        fat_g=item.get("fat_g"),
                        estimated_calories=item.get("estimated_calories", 0),
                    )
                    for item in parsed.get("items", [])
                ],
                assumptions=parsed.get("assumptions", []),
                needs_clarification=parsed.get("needs_clarification", False),
                clarifying_question=parsed.get("clarifying_question"),
                model_name=model,
                prompt_version=IMAGE_MEAL_ESTIMATION_PROMPT_VERSION,
                raw_output=raw_text,
                latency_ms=latency_ms,
                total_protein_g=parsed.get("total_protein_g"),
                total_carbs_g=parsed.get("total_carbs_g"),
                total_fat_g=parsed.get("total_fat_g"),
                estimation_reasoning=parsed.get("estimation_reasoning"),
            )

    async def transcribe_audio(self, audio_url: str) -> SpeechTranscriptionResult:
        model = settings.OPENROUTER_AUDIO_MODEL
        
        try:
            if not (audio_url.startswith("http://") or audio_url.startswith("https://")):
                clean_path = audio_url.lstrip("/")
                if not clean_path.startswith("app/"):
                    local_path = f"app/{clean_path}"
                else:
                    local_path = clean_path
                
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
                
                audio_base64 = base64.b64encode(audio_content).decode("utf-8")
            else:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    audio_res = await client.get(audio_url)
                    audio_res.raise_for_status()
                    content_type = audio_res.headers.get("content-type", "audio/mp3")
                    if content_type == "application/octet-stream":
                        if audio_url.endswith(".m4a"):
                            content_type = "audio/m4a"
                        elif audio_url.endswith(".wav"):
                            content_type = "audio/wav"
                        else:
                            content_type = "audio/mp3"
                    audio_base64 = base64.b64encode(audio_res.content).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to download or load audio from {audio_url}: {e}")
            raise SpeechTranscriptionError(f"Could not retrieve spoken audio for transcription: {str(e)}")
            
        # For OpenRouter, multimodal models accept audio passed in a data URI just like images
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Transcribe the attached audio description of a meal verbatim."
                    },
                    {
                        "type": "image_url",  # OpenRouter uses standard content block structure for multimodal files
                        "image_url": {
                            "url": f"data:{content_type};base64,{audio_base64}"
                        }
                    }
                ]
            }
        ]
        
        raw_text, latency_ms = await self._post_openrouter(
            model=model,
            system_prompt=VOICE_TRANSCRIPTION_SYSTEM_PROMPT,
            messages=messages,
            response_format={"type": "json_object"},
        )
        
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            parsed = await self._repair_json(raw_text, str(e), SpeechTranscriptionResult)
            
        try:
            result = SpeechTranscriptionResult(
                transcript=parsed.get("transcript", ""),
                confidence=parsed.get("confidence", "medium"),
                language=parsed.get("language"),
                model_name=model,
                raw_output=raw_text,
                latency_ms=latency_ms,
            )
            return result
        except ValidationError as ve:
            parsed = await self._repair_json(raw_text, str(ve), SpeechTranscriptionResult)
            return SpeechTranscriptionResult(
                transcript=parsed.get("transcript", ""),
                confidence=parsed.get("confidence", "medium"),
                language=parsed.get("language"),
                model_name=model,
                raw_output=raw_text,
                latency_ms=latency_ms,
            )

    async def suggest_meal_completion(
        self,
        completion_req: MealCompletionRequest,
        user_context: UserContext | None = None,
    ) -> MealCompletionResult:
        model = settings.OPENROUTER_TEXT_MODEL
        
        context_str = ""
        if user_context:
            context_parts = []
            if user_context.sex or user_context.age or user_context.height_cm or user_context.weight_kg:
                profile = []
                if user_context.sex: profile.append(f"Sex: {user_context.sex}")
                if user_context.age: profile.append(f"Age: {user_context.age}")
                if user_context.height_cm: profile.append(f"Height: {user_context.height_cm} cm")
                if user_context.weight_kg: profile.append(f"Weight: {user_context.weight_kg} kg")
                context_parts.append(f"User Physical Profile: {', '.join(profile)}")
            if user_context.daily_calorie_goal:
                context_parts.append(f"User Daily Calorie Goal: {user_context.daily_calorie_goal} kcal")
            if context_parts:
                context_str = "\n\nUSER CONTEXT:\n" + "\n".join(context_parts)
                
        req_info = (
            f"Remaining Calories: {completion_req.remaining_calories} kcal\n"
            f"Consumed Calories: {completion_req.consumed_calories} kcal\n"
            f"Daily Calorie Goal: {completion_req.daily_goal} kcal\n"
            f"Consumed Protein: {completion_req.consumed_protein_g}g\n"
            f"Consumed Carbs: {completion_req.consumed_carbs_g}g\n"
            f"Consumed Fat: {completion_req.consumed_fat_g}g\n"
            f"Meals Eaten Today: {', '.join(completion_req.meals_eaten_today) if completion_req.meals_eaten_today else 'None'}"
        )
        
        # Map locale to output language for prompt
        lang_map = {
            "it": "Italian",
            "es": "Spanish",
            "zh": "Chinese",
            "ja": "Japanese",
            "ar": "Arabic",
            "en": "English",
        }
        output_lang = "English"
        if user_context and user_context.locale:
            primary = user_context.locale.split("-")[0].split("_")[0].strip().lower()
            output_lang = lang_map.get(primary, "English")

        messages = [
            {
                "role": "user",
                "content": (
                    f"Suggest 3 meals/recipes to complete my day based on this info:\n{req_info}{context_str}\n\n"
                    f"CRITICAL: You must output all text fields (meal_name, description, ingredients, preparation_hint, reasoning, "
                    f"daily_context_summary, macro_balance_note) in the following language: {output_lang}."
                )
            }
        ]
        
        raw_text, latency_ms = await self._post_openrouter(
            model=model,
            system_prompt=MEAL_COMPLETION_SYSTEM_PROMPT,
            messages=messages,
            response_format={"type": "json_object"},
        )
        
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            parsed = await self._repair_json(raw_text, str(e), MealCompletionResult)
            
        try:
            suggestions = [
                MealSuggestionItem(
                    meal_name=item.get("meal_name", ""),
                    description=item.get("description", ""),
                    estimated_calories=item.get("estimated_calories", 0),
                    protein_g=item.get("protein_g", 0.0),
                    carbs_g=item.get("carbs_g", 0.0),
                    fat_g=item.get("fat_g", 0.0),
                    ingredients=item.get("ingredients", []),
                    preparation_hint=item.get("preparation_hint", ""),
                    reasoning=item.get("reasoning", ""),
                    meal_type=item.get("meal_type", "snack"),
                    difficulty=item.get("difficulty", "easy"),
                    prep_time_minutes=item.get("prep_time_minutes", 0),
                )
                for item in parsed.get("suggestions", [])
            ]
            result = MealCompletionResult(
                suggestions=suggestions,
                daily_context_summary=parsed.get("daily_context_summary", ""),
                macro_balance_note=parsed.get("macro_balance_note", ""),
                model_name=model,
                prompt_version=MEAL_COMPLETION_PROMPT_VERSION,
                raw_output=raw_text,
                latency_ms=latency_ms,
            )
            return result
        except ValidationError as ve:
            parsed = await self._repair_json(raw_text, str(ve), MealCompletionResult)
            suggestions = [
                MealSuggestionItem(
                    meal_name=item.get("meal_name", ""),
                    description=item.get("description", ""),
                    estimated_calories=item.get("estimated_calories", 0),
                    protein_g=item.get("protein_g", 0.0),
                    carbs_g=item.get("carbs_g", 0.0),
                    fat_g=item.get("fat_g", 0.0),
                    ingredients=item.get("ingredients", []),
                    preparation_hint=item.get("preparation_hint", ""),
                    reasoning=item.get("reasoning", ""),
                    meal_type=item.get("meal_type", "snack"),
                    difficulty=item.get("difficulty", "easy"),
                    prep_time_minutes=item.get("prep_time_minutes", 0),
                )
                for item in parsed.get("suggestions", [])
            ]
            return MealCompletionResult(
                suggestions=suggestions,
                daily_context_summary=parsed.get("daily_context_summary", ""),
                macro_balance_note=parsed.get("macro_balance_note", ""),
                model_name=model,
                prompt_version=MEAL_COMPLETION_PROMPT_VERSION,
                raw_output=raw_text,
                latency_ms=latency_ms,
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
            text, _ = await self._post_openrouter(
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
        import json
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
            text, _ = await self._post_openrouter(
                model=settings.OPENROUTER_TEXT_MODEL,
                system_prompt=PATTERN_INSIGHTS_SYSTEM_PROMPT,
                messages=messages,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(text)
            return parsed.get("patterns", [])
        except Exception:
            return ["Log more meals to unlock AI pattern insights."]
