import datetime as dt

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class IngredientImage(Base):
    """One generated illustration per canonical ingredient name (C29), shared
    across every user and meal. ``canonical_key`` comes from
    ``canonicalize_food_name`` (the same normalizer the food-memory cache
    uses), so "mozzarella" / "Mozzarella" / "mozzarelle" all resolve to the
    same cached image. A row whose ``prompt_version`` no longer matches the
    current style is treated as stale and regenerated in place — see
    IngredientImageService."""

    __tablename__ = "ingredient_images"
    __table_args__ = (UniqueConstraint("canonical_key", name="uq_ingredient_image_canonical_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    # First-seen human-readable name, kept only for admin/debugging legibility.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    image_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
