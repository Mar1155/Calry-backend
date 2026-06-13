from app.core.exceptions import CalryException


class AIProviderError(CalryException):
    """Exception raised when downstream AI APIs (Gemini/OpenAI) fail with HTTP or connection errors."""

    def __init__(self, message: str = "AI service is temporarily unavailable. Please try again.", details: dict | None = None):
        super().__init__(
            message=message,
            status_code=502,
            error_code="AI_PROVIDER_ERROR",
            details=details,
        )


class AIInvalidResponseError(CalryException):
    """Exception raised when the model's response format/schema is invalid and JSON repair fails."""

    def __init__(self, message: str = "I couldn't parse the response from the AI. Please try again.", details: dict | None = None):
        super().__init__(
            message=message,
            status_code=422,
            error_code="AI_INVALID_RESPONSE",
            details=details,
        )


class AIProcessingError(CalryException):
    """General exception raised when processing an AI estimation request fails."""

    def __init__(self, message: str = "I couldn't estimate this meal. Try describing it in words.", details: dict | None = None):
        super().__init__(
            message=message,
            status_code=422,
            error_code="AI_PROCESSING_ERROR",
            details=details,
        )


class ImageAnalysisError(CalryException):
    """Exception raised when vision analysis on food photos fails or images are unreadable."""

    def __init__(self, message: str = "The photo is unclear. Add a short description?", details: dict | None = None):
        super().__init__(
            message=message,
            status_code=422,
            error_code="IMAGE_ANALYSIS_ERROR",
            details=details,
        )


class SpeechTranscriptionError(CalryException):
    """Exception raised when speech-to-text transcription fails."""

    def __init__(self, message: str = "I couldn't understand the audio. Try again or type it.", details: dict | None = None):
        super().__init__(
            message=message,
            status_code=422,
            error_code="SPEECH_TRANSCRIPTION_ERROR",
            details=details,
        )
