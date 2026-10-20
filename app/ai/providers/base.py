from abc import ABC, abstractmethod

from app.ai.schemas.meal_completion import MealCompletionRequest, MealCompletionResult
from app.ai.schemas.meal_estimate import MealEstimateResult, SpeechTranscriptionResult, UserContext
from app.ai.schemas.meal_review import (
    DayReviewRequest,
    DayReviewResult,
    MealOccasionReviewRequest,
    MealOccasionReviewResult,
)


class BaseAIProvider(ABC):
    """Abstract interface defining the requirements for AI model providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Returns the name of the provider (e.g. 'gemini', 'openai')."""
        pass

    @abstractmethod
    async def estimate_meal_from_text(
        self,
        input_text: str,
        user_context: UserContext | None = None,
        is_voice: bool = False,
        additional_context: str | None = None,
    ) -> MealEstimateResult:
        """Estimates nutritional data from a text meal description.

        is_voice flags that the text is an ASR transcript, so the model should
        infer through disfluencies rather than ask for clarification.
        """
        pass

    @abstractmethod
    async def estimate_meal_from_image(
        self,
        image_url: str,
        user_context: UserContext | None = None,
        optional_hint: str | None = None,
        additional_context: str | None = None,
    ) -> MealEstimateResult:
        """Estimates nutritional data from a food photo URL, with optional text hint."""
        pass

    @abstractmethod
    async def refine_meal_estimate(
        self,
        meal_snapshot: dict,
        user_refinement: str,
        source_type: str,
        user_context: UserContext | None = None,
    ) -> MealEstimateResult:
        """Revises an existing structured meal estimate from conversational detail."""
        pass

    @abstractmethod
    async def transcribe_audio(self, audio_url: str) -> SpeechTranscriptionResult:
        """Transcribes a spoken meal description audio URL verbatim."""
        pass

    @abstractmethod
    async def suggest_meal_completion(
        self,
        completion_req: MealCompletionRequest,
        user_context: UserContext | None = None,
    ) -> MealCompletionResult:
        """Suggests meals/recipes to complete the remaining calorie target."""
        pass

    @abstractmethod
    async def review_meal_occasion(
        self,
        review_req: MealOccasionReviewRequest,
        user_context: UserContext | None = None,
        locale: str = "en",
    ) -> MealOccasionReviewResult:
        """Reflects on one already-logged meal occasion (breakfast/lunch/dinner/snack)."""
        pass

    @abstractmethod
    async def review_day(
        self,
        review_req: DayReviewRequest,
        user_context: UserContext | None = None,
        locale: str = "en",
    ) -> DayReviewResult:
        """Reflects on the whole day's logged meals, peak-end style."""
        pass
