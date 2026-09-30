import datetime as dt

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class IngredientTranslation(Base):
    """Memoizes a name's English translation for the ingredient-image cache
    (C29). density_table's EN/IT keyword buckets catch most repeat phrasings
    for free; anything left over gets translated to English once via the
    model and stored here, keyed by ``source_key`` (canonicalize_food_name of
    the cleaned original name) — every later occurrence of that same source
    name, in any request, reuses the stored ``english_name`` instead of
    paying for another translation call."""

    __tablename__ = "ingredient_translations"
    __table_args__ = (UniqueConstraint("source_key", name="uq_ingredient_translation_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    # First-seen original-language name, kept only for admin/debugging legibility.
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    english_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
