"""Tests for packaged-food barcode lookup, PostgreSQL cache, and meal logging."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def _user(client: AsyncClient, name: str) -> dict:
    headers = {"Authorization": f"Bearer mock_token_{name}"}
    await client.get("/api/v1/users/me", headers=headers)
    return headers


SAMPLE_OFF_BARILLA_SPAGHETTI = {
    "code": "8076800195057",
    "product_name": "Spaghetti n.5",
    "product_name_it": "Spaghetti n.5",
    "product_name_en": "Spaghetti No. 5",
    "brands": "Barilla",
    "image_front_url": "https://images.openfoodfacts.org/images/products/807/680/019/5057/front_it.jpg",
    "serving_size": "1 porzione (85 g)",
    "serving_quantity": 85,
    "product_quantity": 500,
    "nutriscore_grade": "a",
    "nutriments": {
        "energy-kcal_100g": 359,
        "proteins_100g": 13.0,
        "carbohydrates_100g": 70.9,
        "fat_100g": 2.0,
    },
}


@pytest.mark.asyncio
async def test_barcode_lookup_caches_product_and_serves_localized_name(client: AsyncClient) -> None:
    headers = await _user(client, "barcode_lookup_user")

    with patch(
        "app.services.barcode_lookup_service.BarcodeLookupService._fetch_open_food_facts",
        new_callable=AsyncMock,
        return_value=SAMPLE_OFF_BARILLA_SPAGHETTI,
    ) as mock_off:
        resp1 = await client.get(
            "/api/v1/meals/barcode/8076800195057",
            headers={**headers, "Accept-Language": "it-IT"},
        )
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["code"] == "8076800195057"
        assert body1["product_name"] == "Spaghetti n.5"
        assert body1["brand"] == "Barilla"
        assert body1["calories_per_100g"] == 359.0
        assert body1["protein_per_100g"] == 13.0
        assert body1["carbs_per_100g"] == 70.9
        assert body1["fat_per_100g"] == 2.0
        assert body1["serving_weight_grams"] == 85
        assert body1["default_weight_grams"] == 85
        assert mock_off.await_count == 1

        # Second request hits PostgreSQL cache without calling Open Food Facts
        resp2 = await client.get(
            "/api/v1/meals/barcode/8076800195057",
            headers={**headers, "Accept-Language": "en-US"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["product_name"] == "Spaghetti No. 5"
        assert mock_off.await_count == 1


@pytest.mark.asyncio
async def test_barcode_log_creates_meal_and_does_not_consume_ai_quota(client: AsyncClient) -> None:
    headers = await _user(client, "barcode_log_user")

    quota_before = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()

    with patch(
        "app.services.barcode_lookup_service.BarcodeLookupService._fetch_open_food_facts",
        new_callable=AsyncMock,
        return_value=SAMPLE_OFF_BARILLA_SPAGHETTI,
    ):
        resp = await client.post(
            "/api/v1/meals/barcode",
            json={
                "code": "8076800195057",
                "weight_grams": 85,
                "meal_category": "lunch",
                "client_request_id": "barcode-req-001",
            },
            headers={**headers, "Accept-Language": "it"},
        )

    assert resp.status_code == 201
    meal = resp.json()
    expected_off_img = SAMPLE_OFF_BARILLA_SPAGHETTI["image_front_url"]
    assert meal["source_type"] == "barcode"
    assert meal["meal_name"] == "Spaghetti n.5 (Barilla)"
    assert meal["meal_category"] == "lunch"
    assert meal["image_url"] == expected_off_img
    # 85g * 359 kcal / 100g = 305.15 -> 305 kcal
    assert meal["estimated_calories"] == 305
    assert len(meal["items"]) == 1
    assert meal["items"][0]["weight_grams"] == 85
    assert meal["items"][0]["calories_per_100g"] == 359.0
    assert meal["items"][0]["image_url"] == expected_off_img

    # Confirming the meal via PATCH (which replaces items) preserves the Open Food Facts image_url
    patch_resp = await client.patch(
        f"/api/v1/meals/{meal['id']}",
        json={
            "is_confirmed": True,
            "items": [
                {
                    "name": meal["items"][0]["name"],
                    "quantity_estimate": "100 g",
                    "weight_grams": 100,
                    "calories_per_100g": 359.0,
                    "protein_g": 13.0,
                    "carbs_g": 70.9,
                    "fat_g": 2.0,
                }
            ],
        },
        headers=headers,
    )
    assert patch_resp.status_code == 200
    patched_meal = patch_resp.json()
    assert patched_meal["image_url"] == expected_off_img
    assert patched_meal["items"][0]["image_url"] == expected_off_img

    # Product without an Open Food Facts image keeps image_url=None (so client shows standard barcode icon)
    no_img_product = {**SAMPLE_OFF_BARILLA_SPAGHETTI, "code": "8076800195099", "image_front_url": None}
    with patch(
        "app.services.barcode_lookup_service.BarcodeLookupService._fetch_open_food_facts",
        new_callable=AsyncMock,
        return_value=no_img_product,
    ):
        no_img_resp = await client.post(
            "/api/v1/meals/barcode",
            json={"code": "8076800195099", "weight_grams": 100},
            headers=headers,
        )
    assert no_img_resp.status_code == 201
    no_img_meal = no_img_resp.json()
    assert no_img_meal["image_url"] is None
    assert no_img_meal["items"][0]["image_url"] is None

    # AI quota must remain untouched
    quota_after = (await client.get("/api/v1/premium/ai-quota", headers=headers)).json()
    assert quota_after["remaining"] == quota_before["remaining"]


@pytest.mark.asyncio
async def test_barcode_invalid_format_returns_422_and_unknown_returns_404(client: AsyncClient) -> None:
    headers = await _user(client, "barcode_err_user")

    bad_resp = await client.get("/api/v1/meals/barcode/abc-123", headers=headers)
    assert bad_resp.status_code == 422
    assert bad_resp.json()["detail"]["code"] == "INVALID_BARCODE_FORMAT"

    with patch(
        "app.services.barcode_lookup_service.BarcodeLookupService._fetch_open_food_facts",
        new_callable=AsyncMock,
        return_value=None,
    ):
        missing_resp = await client.get("/api/v1/meals/barcode/8009999888877", headers=headers)
        assert missing_resp.status_code == 404
        assert missing_resp.json()["detail"]["code"] == "BARCODE_NOT_FOUND"
