import datetime as dt
from pydantic import BaseModel, ConfigDict


class FoodMemoryItemSnapshot(BaseModel):
    name: str
    estimated_calories: int
    quantity_estimate: str | None = None
    weight_grams: int | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


class FoodMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    display_name: str
    learned_calories: int
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    use_count: int
    last_used_at: dt.datetime
