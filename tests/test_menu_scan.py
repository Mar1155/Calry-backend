import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.meal_estimate import MealEstimateItem
from app.ai.schemas.menu_scan import (
    MenuCategoriesAIRequest,
    MenuCategoriesAIResult,
    MenuCategoryAIItem,
    MenuDishSuggestionItem,
    MenuScanAIRequest,
    MenuScanAIResult,
)
from app.ai.services.menu_url_loader import (
    LoadedMenuContent,
    MenuUrlError,
    extract_text_from_html,
    validate_public_http_url,
)
from app.models.food_memory import UserFoodMemory
from app.models.meal import Meal
from app.models.user import User


async def _create_user(client: AsyncClient, tag: str, *, pro: bool = False) -> tuple[dict[str, str], int]:
    headers = {"Authorization": f"Bearer mock_token_{tag}", "Accept-Language": "it-IT"}
    res = await client.get("/api/v1/users/me", headers=headers)
    assert res.status_code == 200
    user_id = res.json()["id"]
    if pro:
        sync_res = await client.post(
            "/api/v1/premium/sync",
            headers=headers,
            json={
                "is_premium": True,
                "entitlement": "pro_subscription",
                "expires_at": "2030-01-01T00:00:00Z",
                "revenuecat_app_user_id": f"rc_{tag}",
            },
        )
        assert sync_res.status_code == 200
    return headers, user_id


def _sample_menu_result() -> MenuScanAIResult:
    return MenuScanAIResult(
        is_menu=True,
        daily_context_summary="Hai consumato 950 kcal su 2000 kcal, con spazio per proteine.",
        suggestions=[
            MenuDishSuggestionItem(
                angle="best_fit",
                dish_name="Filetto di orata alla griglia con verdure",
                original_menu_name="Orata ai ferri e zucchine",
                description="Orata fresca alla piastra con zucchine e melanzane grigliate.",
                estimated_calories=480,
                protein_g=42.0,
                carbs_g=14.0,
                fat_g=26.0,
                why_for_you_today="Centra le proteine che ti mancano oggi restando leggero.",
                practical_tip=None,
                meal_category="dinner",
                items=[
                    MealEstimateItem(
                        name="Filetto di orata",
                        quantity_estimate="200 g",
                        weight_grams=200,
                        calories_per_100g=165.0,
                        protein_g=38.0,
                        carbs_g=0.0,
                        fat_g=16.0,
                        estimated_calories=330,
                    ),
                    MealEstimateItem(
                        name="Verdure grigliate con olio",
                        quantity_estimate="180 g",
                        weight_grams=180,
                        calories_per_100g=83.3,
                        protein_g=4.0,
                        carbs_g=14.0,
                        fat_g=10.0,
                        estimated_calories=150,
                    ),
                ],
            ),
            MenuDishSuggestionItem(
                angle="for_you",
                dish_name="Poke di salmone e avocado",
                original_menu_name="Salmon Poke Bowl",
                description="Riso basmati, salmone crudo, avocado ed edamame.",
                estimated_calories=620,
                protein_g=34.0,
                carbs_g=64.0,
                fat_g=24.0,
                why_for_you_today="Riprende il salmone che scegli spesso ed è ben bilanciato.",
                practical_tip=None,
                meal_category="dinner",
                items=[
                    MealEstimateItem(
                        name="Salmone crudo",
                        quantity_estimate="130 g",
                        weight_grams=130,
                        calories_per_100g=208.0,
                        protein_g=26.0,
                        carbs_g=0.0,
                        fat_g=17.0,
                        estimated_calories=270,
                    ),
                    MealEstimateItem(
                        name="Riso e avocado",
                        quantity_estimate="250 g",
                        weight_grams=250,
                        calories_per_100g=140.0,
                        protein_g=8.0,
                        carbs_g=64.0,
                        fat_g=7.0,
                        estimated_calories=350,
                    ),
                ],
            ),
            MenuDishSuggestionItem(
                angle="mindful_pleasure",
                dish_name="Tonnarelli cacio e pepe",
                original_menu_name="Tonnarelli Cacio e Pepe",
                description="Pasta fresca fatta in casa con pecorino romano e pepe nero.",
                estimated_calories=780,
                protein_g=24.0,
                carbs_g=88.0,
                fat_g=36.0,
                why_for_you_today="Il piatto più goloso del menu, comunque gestibile nella tua giornata.",
                practical_tip="Abbinalo a un'insalata verde o dividi un antipasto per restare comodo.",
                meal_category="dinner",
                items=[
                    MealEstimateItem(
                        name="Tonnarelli freschi",
                        quantity_estimate="220 g",
                        weight_grams=220,
                        calories_per_100g=230.0,
                        protein_g=10.0,
                        carbs_g=86.0,
                        fat_g=10.0,
                        estimated_calories=506,
                    ),
                    MealEstimateItem(
                        name="Pecorino romano",
                        quantity_estimate="70 g",
                        weight_grams=70,
                        calories_per_100g=391.4,
                        protein_g=14.0,
                        carbs_g=2.0,
                        fat_g=26.0,
                        estimated_calories=274,
                    ),
                ],
            ),
        ],
        model_name="gemini-2.5-flash",
        prompt_version="menu_scan_v1",
        latency_ms=320,
    )


@pytest.mark.asyncio
async def test_menu_scan_and_order_require_pro(client: AsyncClient) -> None:
    headers, _ = await _create_user(client, "menu_free", pro=False)

    scan_res = await client.post(
        "/api/v1/meals/menu-scan",
        headers=headers,
        json={"image_urls": ["/static/uploads/menu1.jpg"]},
    )
    assert scan_res.status_code == 403
    assert scan_res.json()["detail"]["code"] == "FEATURE_LOCKED_PRO"
    assert scan_res.json()["detail"]["feature"] == "menu_scan"

    order_res = await client.post(
        "/api/v1/meals/menu-order",
        headers=headers,
        json={
            "meal_name": "Orata ai ferri",
            "meal_category": "dinner",
            "items": [
                {
                    "name": "Orata",
                    "quantity_estimate": "200 g",
                    "weight_grams": 200,
                    "calories_per_100g": 165.0,
                }
            ],
        },
    )
    assert order_res.status_code == 403
    assert order_res.json()["detail"]["code"] == "FEATURE_LOCKED_PRO"


@pytest.mark.asyncio
async def test_menu_scan_multipage_passes_context_and_memory(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, user_id = await _create_user(client, "menu_pro", pro=True)

    user = await db_session.get(User, user_id)
    assert user is not None
    user.daily_calorie_goal = 2000
    db_session.add(
        Meal(
            user_id=user_id,
            source_type="text",
            original_input="Yogurt e avena",
            meal_name="Colazione proteica",
            meal_category="breakfast",
            estimated_calories=400,
            confirmed_calories=400,
            total_protein_g=25.0,
            total_carbs_g=45.0,
            total_fat_g=10.0,
            created_at=dt.datetime.now(dt.UTC),
        )
    )
    db_session.add(
        UserFoodMemory(
            user_id=user_id,
            normalized_name="salmone grigliato",
            canonical_key="salmone grigliato",
            display_name="Salmone grigliato",
            learned_calories=380,
            is_favorite=True,
            use_count=4,
        )
    )
    await db_session.commit()

    with patch(
        "app.api.v1.routes.meal_completion.AICalorieEstimationService.scan_menu",
        new_callable=AsyncMock,
        return_value=_sample_menu_result(),
    ) as mock_scan:
        res = await client.post(
            "/api/v1/meals/menu-scan",
            headers=headers,
            json={
                "image_urls": [
                    "/static/uploads/page1.jpg",
                    "/static/uploads/page2.jpg",
                    "/static/uploads/page3.jpg",
                ],
                "user_note": "Voglia di pesce leggero",
                "meal_type": "dinner",
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert len(body["suggestions"]) == 3
    assert [s["angle"] for s in body["suggestions"]] == [
        "best_fit",
        "for_you",
        "mindful_pleasure",
    ]
    assert body["remaining_calories"] == 1600
    assert body["consumed_calories"] == 400
    assert body["daily_goal"] == 2000

    menu_req: MenuScanAIRequest = mock_scan.await_args.kwargs["menu_req"]
    assert len(menu_req.image_urls) == 3
    assert menu_req.user_note == "Voglia di pesce leggero"
    assert menu_req.requested_meal_type == "dinner"
    assert "Salmone grigliato" in menu_req.favorite_and_regular_foods


@pytest.mark.asyncio
async def test_menu_scan_rejects_non_menu_image(client: AsyncClient) -> None:
    headers, _ = await _create_user(client, "menu_non_menu", pro=True)

    non_menu = MenuScanAIResult(
        is_menu=False,
        suggestions=[],
        daily_context_summary="",
        model_name="gemini-2.5-flash",
        prompt_version="menu_scan_v1",
    )
    with patch(
        "app.api.v1.routes.meal_completion.AICalorieEstimationService.scan_menu",
        new_callable=AsyncMock,
        return_value=non_menu,
    ):
        res = await client.post(
            "/api/v1/meals/menu-scan",
            headers=headers,
            json={"image_urls": ["/static/uploads/plate.jpg"]},
        )

    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "MENU_UNREADABLE"


def test_openrouter_menu_scan_parser_normalizes_ingredients() -> None:
    provider = OpenRouterProvider()
    req = MenuScanAIRequest(
        image_urls=["/static/uploads/menu.jpg"],
        remaining_calories=900,
        consumed_calories=1100,
        daily_goal=2000,
        consumed_protein_g=50.0,
        consumed_carbs_g=120.0,
        consumed_fat_g=40.0,
    )
    parsed = {
        "is_menu": True,
        "daily_context_summary": "Hai 900 kcal rimanenti.",
        "suggestions": [
            {
                "angle": "best_fit",
                "dish_name": "Pollo alla griglia",
                "description": "Petto di pollo con insalata.",
                "estimated_calories": 420,
                "protein_g": 45,
                "carbs_g": 8,
                "fat_g": 18,
                "why_for_you_today": "Alto contenuto proteico.",
                "meal_category": "lunch",
                "items": [
                    {
                        "name": "Petto di pollo",
                        "quantity_estimate": "200 g",
                        "weight_grams": 200,
                        "estimated_calories": 330,
                    },
                    {
                        "name": "Olio e insalata",
                        "quantity_estimate": "100 g",
                        "weight_grams": 100,
                        "estimated_calories": 90,
                    },
                ],
            }
        ],
    }

    result = provider._build_menu_scan_result(parsed, "gemini-2.5-flash", "{}", 100, None, req)
    assert result.is_menu is True
    assert len(result.suggestions) == 1
    suggestion = result.suggestions[0]
    assert suggestion.estimated_calories == 420
    assert len(suggestion.items) == 2
    assert all(item.calories_per_100g is not None and item.weight_grams > 0 for item in suggestion.items)


@pytest.mark.asyncio
async def test_menu_order_creates_pending_meal_and_confirms(client: AsyncClient) -> None:
    headers, _ = await _create_user(client, "menu_order_flow", pro=True)

    payload = {
        "meal_name": "Filetto di orata alla griglia con verdure",
        "meal_category": "dinner",
        "estimation_reasoning": "Centra le proteine di oggi.",
        "client_request_id": "menu-order-req-1",
        "items": [
            {
                "name": "Filetto di orata",
                "quantity_estimate": "200 g",
                "weight_grams": 200,
                "calories_per_100g": 165.0,
                "protein_g": 38.0,
                "carbs_g": 0.0,
                "fat_g": 16.0,
            },
            {
                "name": "Verdure grigliate",
                "quantity_estimate": "180 g",
                "weight_grams": 180,
                "calories_per_100g": 83.3,
                "protein_g": 4.0,
                "carbs_g": 14.0,
                "fat_g": 10.0,
            },
        ],
    }

    res = await client.post("/api/v1/meals/menu-order", headers=headers, json=payload)
    assert res.status_code == 201
    meal = res.json()
    assert meal["source_type"] == "menu"
    assert meal["meal_category"] == "dinner"
    assert meal["confirmed_calories"] is None
    assert meal["estimated_calories"] == 480

    # Idempotent replay returns same meal
    replay = await client.post("/api/v1/meals/menu-order", headers=headers, json=payload)
    assert replay.status_code == 201
    assert replay.json()["id"] == meal["id"]

    # Confirm in 1 tap
    confirm_res = await client.patch(
        f"/api/v1/meals/{meal['id']}",
        headers=headers,
        json={"is_confirmed": True},
    )
    assert confirm_res.status_code == 200
    assert confirm_res.json()["confirmed_calories"] == 480


def test_menu_url_ssrf_and_html_extractor() -> None:
    for blocked in (
        "ftp://example.com/menu",
        "http://localhost/menu",
        "http://127.0.0.1:8000/menu",
        "http://10.0.0.5/menu",
        "http://192.168.1.10/menu",
        "http://169.254.169.254/latest/meta-data",
        "http://service.internal/menu",
    ):
        with pytest.raises(MenuUrlError):
            validate_public_http_url(blocked)

    with patch("app.ai.services.menu_url_loader.socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        assert validate_public_http_url("https://ristorante.example/menu") == "https://ristorante.example/menu"

        mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with pytest.raises(MenuUrlError):
            validate_public_http_url("https://evil.example/menu")

    sample_html = """
    <html>
      <head><title>Trattoria del Sole</title><style>.x{color:red}</style></head>
      <body>
        <script>console.log("ignore me")</script>
        <h1>Antipasti</h1>
        <p>Bruschetta al pomodoro - 6 EUR</p>
        <h2>Primi piatti</h2>
        <ul><li>Spaghetti alle vongole - 16 EUR</li></ul>
      </body>
    </html>
    """
    extracted = extract_text_from_html(sample_html)
    assert "Trattoria del Sole" in extracted
    assert "Antipasti" in extracted
    assert "Bruschetta al pomodoro" in extracted
    assert "Primi piatti" in extracted
    assert "Spaghetti alle vongole" in extracted
    assert "ignore me" not in extracted


@pytest.mark.asyncio
async def test_menu_categories_from_photos_and_digital_url(client: AsyncClient) -> None:
    headers, _ = await _create_user(client, "menu_categories_pro", pro=True)

    categories_result = MenuCategoriesAIResult(
        is_menu=True,
        restaurant_name="Osteria Moderna",
        categories=[
            MenuCategoryAIItem(
                id="antipasti",
                name="Antipasti",
                dish_count=5,
                sample_dishes=["Carpaccio di spigola", "Burrata"],
            ),
            MenuCategoryAIItem(
                id="secondi_pesce",
                name="Secondi di pesce",
                dish_count=4,
                sample_dishes=["Orata ai ferri", "Tataki di tonno"],
            ),
        ],
        model_name="gemini-2.5-flash",
        prompt_version="menu_categories_v1",
        latency_ms=180,
    )

    with (
        patch(
            "app.api.v1.routes.meal_completion.fetch_digital_menu",
            new_callable=AsyncMock,
            return_value=LoadedMenuContent(
                source_url="https://osteria.example/menu",
                text_content="Osteria Moderna\nAntipasti\nCarpaccio di spigola\nSecondi di pesce\nOrata ai ferri",
            ),
        ) as mock_fetch,
        patch(
            "app.api.v1.routes.meal_completion.AICalorieEstimationService.extract_menu_categories",
            new_callable=AsyncMock,
            return_value=categories_result,
        ) as mock_extract,
    ):
        res = await client.post(
            "/api/v1/meals/menu-categories",
            headers=headers,
            json={"menu_url": "https://osteria.example/menu"},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["restaurant_name"] == "Osteria Moderna"
    assert [c["name"] for c in body["categories"]] == ["Antipasti", "Secondi di pesce"]
    mock_fetch.assert_awaited_once_with("https://osteria.example/menu")
    cat_req: MenuCategoriesAIRequest = mock_extract.await_args.kwargs["menu_req"]
    assert "Osteria Moderna" in (cat_req.menu_text or "")

    # Unreachable/blocked URL surfaces MENU_URL_UNREACHABLE
    with patch(
        "app.api.v1.routes.meal_completion.fetch_digital_menu",
        new_callable=AsyncMock,
        side_effect=MenuUrlError("MENU_URL_UNREACHABLE", "Private network blocked"),
    ):
        err_res = await client.post(
            "/api/v1/meals/menu-categories",
            headers=headers,
            json={"menu_url": "http://127.0.0.1/menu"},
        )
    assert err_res.status_code == 422
    assert err_res.json()["detail"]["code"] == "MENU_URL_UNREACHABLE"


@pytest.mark.asyncio
async def test_menu_scan_with_url_and_selected_categories(client: AsyncClient) -> None:
    headers, _ = await _create_user(client, "menu_url_selected", pro=True)

    with (
        patch(
            "app.api.v1.routes.meal_completion.fetch_digital_menu",
            new_callable=AsyncMock,
            return_value=LoadedMenuContent(
                source_url="https://osteria.example/menu",
                text_content="Antipasti\nTartare\nSecondi di pesce\nOrata ai ferri\nDolci\nTiramisù",
            ),
        ),
        patch(
            "app.api.v1.routes.meal_completion.AICalorieEstimationService.scan_menu",
            new_callable=AsyncMock,
            return_value=_sample_menu_result(),
        ) as mock_scan,
    ):
        res = await client.post(
            "/api/v1/meals/menu-scan",
            headers=headers,
            json={
                "menu_url": "https://osteria.example/menu",
                "selected_categories": ["Secondi di pesce"],
                "user_note": "Leggero",
            },
        )

    assert res.status_code == 200
    menu_req: MenuScanAIRequest = mock_scan.await_args.kwargs["menu_req"]
    assert menu_req.menu_url == "https://osteria.example/menu"
    assert menu_req.selected_categories == ["Secondi di pesce"]
    assert "Orata ai ferri" in (menu_req.menu_text or "")
