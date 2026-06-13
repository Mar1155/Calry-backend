from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inference import AIInferenceLog
from app.repositories.base import BaseRepository


class AIInferenceLogRepository(BaseRepository[AIInferenceLog]):
    """Repository handling all AI Inference Logging database records."""

    def __init__(self, db: AsyncSession):
        super().__init__(AIInferenceLog, db)
