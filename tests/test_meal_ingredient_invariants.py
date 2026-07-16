import datetime as dt
import importlib.util
import random
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.services.meal_invariants import normalize_meal_ingredients
from scripts.seed_user_history import FoodItem, MealTemplate, build_meal


def _estimate() -> MealEstimateResult:
    return MealEstimateResult(
        meal_name="Rice bowl",
        estimated_calories=999,
        confidence="high",
        source_type="text",
        items=[
            MealEstimateItem(
                name="Rice",
                quantity_estimate="200 g",
                weight_grams=200,
                calories_per_100g=130,
            )
        ],
        model_name="test-model",
        prompt_version="test-prompt",
    )


def test_normalizer_creates_calculable_ingredient_and_derives_total() -> None:
    items, total = normalize_meal_ingredients(
        [],
        meal_name="Apple",
        target_calories=95,
    )

    assert len(items) == 1
    assert items[0]["name"] == "Apple"
    assert items[0]["weight_grams"] > 0
    assert items[0]["calories_per_100g"] >= 0
    assert total == 95


def test_existing_ingredient_sum_overrides_independent_total() -> None:
    items, total = normalize_meal_ingredients(
        [
            {"name": "Rice", "weight_grams": 200, "calories_per_100g": 130},
            {"name": "Chicken", "weight_grams": 150, "calories_per_100g": 165},
        ],
        meal_name="Rice bowl",
        target_calories=999,
    )

    assert total == sum(round(item["weight_grams"] * item["calories_per_100g"] / 100) for item in items)
    assert total == 508


def test_normalizer_repairs_impossible_density_without_changing_calories() -> None:
    items, total = normalize_meal_ingredients(
        [{"name": "Legacy ingredient", "weight_grams": 10, "calories_per_100g": 1500}],
        meal_name="Legacy meal",
        target_calories=150,
    )

    assert items[0]["weight_grams"] > 10
    assert 0 <= items[0]["calories_per_100g"] <= 900
    assert items[0]["estimated_calories"] == total == 150


def test_history_seed_builds_ingredient_derived_meal() -> None:
    meal = build_meal(
        user_id=1,
        day=dt.date(2026, 7, 16),
        template=MealTemplate(
            "text",
            "Apple and yogurt",
            dt.time(8),
            (FoodItem("Apple", 95), FoodItem("Yogurt", 120)),
        ),
        rng=random.Random(42),
    )

    assert meal.items
    assert all(item.weight_grams > 0 for item in meal.items)
    assert all(0 <= item.calories_per_100g <= 900 for item in meal.items)
    assert meal.estimated_calories == sum(item.estimated_calories for item in meal.items)


@pytest.mark.asyncio
async def test_meal_api_rejects_independent_total_and_empty_ingredients(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer mock_token_ingredient_invariant"}
    await client.get("/api/v1/users/me", headers=headers)
    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_text",
        new_callable=AsyncMock,
    ) as mock_estimate:
        mock_estimate.return_value = _estimate()
        created = await client.post(
            "/api/v1/meals/text",
            json={"text": "rice bowl"},
            headers=headers,
        )
    meal_id = created.json()["id"]
    assert created.json()["estimated_calories"] == 260

    manual_total = await client.patch(
        f"/api/v1/meals/{meal_id}",
        json={"estimated_calories": 999},
        headers=headers,
    )
    empty = await client.patch(
        f"/api/v1/meals/{meal_id}",
        json={"items": []},
        headers=headers,
    )

    assert manual_total.status_code == 422
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_meal_update_derives_and_confirms_total_from_ingredients(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer mock_token_ingredient_update"}
    await client.get("/api/v1/users/me", headers=headers)
    with patch(
        "app.api.v1.routes.meals.AICalorieEstimationService.estimate_from_text",
        new_callable=AsyncMock,
    ) as mock_estimate:
        mock_estimate.return_value = _estimate()
        created = await client.post(
            "/api/v1/meals/text",
            json={"text": "rice bowl"},
            headers=headers,
        )

    updated = await client.patch(
        f"/api/v1/meals/{created.json()['id']}",
        json={
            "is_confirmed": True,
            "items": [
                {
                    "name": "Rice",
                    "quantity_estimate": "250 g",
                    "weight_grams": 250,
                    "calories_per_100g": 130,
                }
            ],
        },
        headers=headers,
    )

    assert updated.status_code == 200
    assert updated.json()["items"][0]["estimated_calories"] == 325
    assert updated.json()["estimated_calories"] == 325
    assert updated.json()["confirmed_calories"] == 325


def test_historical_backfill_repairs_empty_and_partial_meals() -> None:
    migration_path = Path(__file__).parents[1] / "alembic/versions/2026_07_16_0001_meal_ingredient_invariants.py"
    spec = importlib.util.spec_from_file_location("meal_invariant_migration", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE meals (id INTEGER PRIMARY KEY, meal_name TEXT, original_input TEXT NOT NULL, "
                "estimated_calories INTEGER NOT NULL, confirmed_calories INTEGER)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE meal_items (id INTEGER PRIMARY KEY AUTOINCREMENT, meal_id INTEGER NOT NULL, "
                "name TEXT, quantity_estimate TEXT, weight_grams INTEGER, calories_per_100g FLOAT, "
                "created_at DATETIME)"
            )
        )
        connection.execute(
            sa.text("INSERT INTO meals VALUES (1, 'Apple', 'apple', 95, NULL), " "(2, 'Lunch', 'lunch', 300, 350)")
        )
        connection.execute(
            sa.text(
                "INSERT INTO meal_items (meal_id, name, quantity_estimate, weight_grams, calories_per_100g) "
                "VALUES (2, 'Rice', '100 g', 100, 100), (2, 'Chicken', NULL, NULL, NULL)"
            )
        )

        migration._backfill_historical_meals(connection)

        rows = (
            connection.execute(
                sa.text(
                    "SELECT m.id, m.estimated_calories, m.confirmed_calories, COUNT(mi.id) AS item_count, "
                    "SUM(CAST(FLOOR(mi.weight_grams * mi.calories_per_100g / 100.0 + 0.5) AS INTEGER)) AS item_total, "
                    "MIN(mi.weight_grams) AS min_weight, MIN(mi.calories_per_100g) AS min_density, "
                    "MAX(mi.calories_per_100g) AS max_density "
                    "FROM meals m JOIN meal_items mi ON mi.meal_id = m.id GROUP BY m.id ORDER BY m.id"
                )
            )
            .mappings()
            .all()
        )

    assert len(rows) == 2
    for row in rows:
        assert row["item_count"] >= 1
        assert row["min_weight"] > 0
        assert row["min_density"] >= 0
        assert row["max_density"] <= 900
        assert row["estimated_calories"] == row["item_total"]
        if row["confirmed_calories"] is not None:
            assert row["confirmed_calories"] == row["item_total"]
