import datetime as dt

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import Base

# Snapshot schema epoch for `items_snapshot`. Bump this whenever a change to the
# estimation prompts/schema materially changes what a *single* item is allowed
# to represent, so stale snapshots can be told apart from current ones.
# 1 = pre ingredient-decomposition (C20): a whole composite dish could be
#     squashed into one item (e.g. "Pizza Napoletana").
# 2 = post ingredient-decomposition: a lone item is only ever a genuinely
#     single-ingredient food or a branded product eaten as sold.
CURRENT_ITEMS_SNAPSHOT_VERSION = 2


class UserFoodMemory(Base):
    __tablename__ = "user_food_memory"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_name", name="uq_user_food_memory"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    normalized_name = Column(String(500), nullable=False)
    # Canonical dedup/lookup key (C4): NFKC-folded, token-sorted, filler-stripped.
    # Used for the pre-inference cache match; backfilled lazily on confirm.
    canonical_key = Column(String(500), nullable=True, index=True)
    display_name = Column(String(500), nullable=False)

    learned_calories = Column(Integer, nullable=False)
    protein_g = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)

    items_snapshot = Column(JSON, nullable=True)
    # NULL = legacy snapshot captured before ingredient decomposition (C21);
    # the cache lookup treats a lone-item legacy snapshot as a miss so it gets
    # re-decomposed and re-stamped on the next confirm. See
    # CURRENT_ITEMS_SNAPSHOT_VERSION above.
    items_snapshot_version = Column(Integer, nullable=True)

    use_count = Column(Integer, nullable=False, default=1)
    is_favorite = Column(Boolean, nullable=False, default=False)
    last_used_at = Column(DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC))

    user = relationship("User", back_populates="food_memories")
