"""Ingredient image generation and cache (C29): one illustration per
canonical ingredient name, generated via recraft/recraft-v4.1-flash on
OpenRouter and reused by every later meal with the same ingredient."""
import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.ai.errors import AIInvalidResponseError, AIProviderError
from app.ai.prompts.ingredient_image import (
    INGREDIENT_IMAGE_PROMPT_VERSION,
    build_ingredient_image_prompt,
)
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.ingredient_image import IngredientImageResult
from app.ai.services.ingredient_image_service import (
    IngredientImageService,
    _cache_target,
    _strip_parenthetical,
)
from app.core.config import settings
from app.core.text_normalization import canonicalize_food_name
from app.models.inference import AIInferenceLog
from app.models.ingredient_image import IngredientImage
from app.models.user import User

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

# ---- cache-key bucketing (reduces regeneration of the "same" ingredient) --


def test_strip_parenthetical_drops_a_clarifying_aside():
    assert _strip_parenthetical("Formaggio Grattugiato (Parmigiano/Grana)") == "Formaggio Grattugiato"
    assert _strip_parenthetical("Salsa (fatta in casa)") == "Salsa"
    assert _strip_parenthetical("Basilico") == "Basilico"  # no parens: unchanged


@pytest.mark.parametrize(
    "names",
    [
        ("Pasta Di Semola Cotta", "Spaghetti", "Penne", "pasta"),
        ("Formaggio Grattugiato (Parmigiano/Grana)", "Parmigiano Reggiano", "cheddar cheese", "mozzarella"),
        ("Olio Extravergine Di Oliva", "olive oil", "olio"),
        ("Riso", "risotto", "steamed rice"),
    ],
)
def test_variant_phrasings_of_the_same_ingredient_share_one_cache_key(names):
    keys = {_cache_target(n)[0] for n in names}
    assert len(keys) == 1, f"expected one shared key, got {keys}"


def test_bucketing_bridges_english_and_italian_for_the_same_ingredient():
    # The user's actual worry: the same real ingredient, named differently by
    # the model depending on the meal's output language, should still share
    # one cached illustration.
    assert _cache_target("olive oil")[0] == _cache_target("Olio Extravergine Di Oliva")[0]
    assert _cache_target("grated cheese")[0] == _cache_target("Formaggio Grattugiato")[0]


def test_visually_distinct_bucket_members_do_not_share_a_key():
    # Fruit/vegetable buckets are excluded from sharing: an apple and a
    # banana look different enough that reusing one icon would read as wrong.
    assert _cache_target("apple")[0] != _cache_target("banana")[0]
    assert _cache_target("broccoli")[0] != _cache_target("bell pepper")[0]


def test_an_unmatched_ingredient_falls_back_to_its_own_canonical_name():
    canonical, generation_name = _cache_target("rapa rossa")
    assert canonical == canonicalize_food_name("rapa rossa")
    assert generation_name == "rapa rossa"


@pytest.mark.asyncio
async def test_bucket_sharing_avoids_a_second_model_call(db_session):
    """End-to-end: two meals name the same real ingredient differently; only
    the first pays for a generation, and the model is asked for the bucket's
    generic name, not either meal's specific phrasing."""
    provider = OpenRouterProvider()
    generate = AsyncMock(return_value=_result())
    uploaded = {"url": "https://cdn.example/pasta.png", "storage": "local"}
    with (
        patch.object(provider, "generate_ingredient_image", generate),
        patch("app.ai.services.ingredient_image_service.save_generated_asset", AsyncMock(return_value=uploaded)),
    ):
        service = IngredientImageService(db_session, provider)
        first = await service.get_or_generate("Pasta Di Semola Cotta")
        second = await service.get_or_generate("Penne al pomodoro")

    assert first == second == uploaded["url"]
    generate.assert_awaited_once_with("Cooked pasta")


# ---- prompt --------------------------------------------------------------


def test_prompt_is_fixed_style_with_only_the_name_varying():
    basil = build_ingredient_image_prompt("basilico")
    tomato = build_ingredient_image_prompt("pomodoro")
    assert "basilico" in basil and "pomodoro" not in basil
    assert "pomodoro" in tomato
    # Same style sentence shared by every ingredient (minus the name itself).
    assert basil.split(".", 1)[1] == tomato.split(".", 1)[1]


def test_prompt_collapses_whitespace_in_the_name():
    assert "  " not in build_ingredient_image_prompt("  extra   virgin   olive oil  ")


# ---- response parsing ------------------------------------------------------


def test_extract_generated_image_reads_the_images_array():
    response = {
        "choices": [{"message": {"images": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}}],
        "usage": {"prompt_tokens": 12},
    }
    url, usage = OpenRouterProvider._extract_generated_image(response)
    assert url == "data:image/png;base64,AA=="
    assert usage == {"prompt_tokens": 12}


def test_extract_generated_image_falls_back_to_content_parts():
    response = {"choices": [{"message": {"content": [{"type": "image_url", "url": "https://cdn.example/x.png"}]}}]}
    url, usage = OpenRouterProvider._extract_generated_image(response)
    assert url == "https://cdn.example/x.png"
    assert usage is None


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"images": []}}]},
        {"choices": [{"message": {"images": [{"type": "image_url"}]}}]},
    ],
)
def test_extract_generated_image_returns_none_when_absent(response):
    assert OpenRouterProvider._extract_generated_image(response) is None


# ---- provider: generate_ingredient_image -----------------------------------


@pytest.mark.asyncio
async def test_generate_ingredient_image_decodes_a_data_uri():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "recraft/recraft-v4.1-flash"
        # Verified live: recraft/recraft-v4.1-flash 404s on OpenRouter the
        # moment "text" is requested alongside "image" ("No endpoints found
        # that support the requested output modalities: image, text").
        assert body["modalities"] == ["image"]
        b64 = base64.b64encode(_PNG_1PX).decode()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}]}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
        ):
            result = await OpenRouterProvider().generate_ingredient_image("basilico")
    finally:
        await client.aclose()

    assert result.image_bytes == _PNG_1PX
    assert result.content_type == "image/png"
    assert result.model_name == "recraft/recraft-v4.1-flash"
    assert result.prompt_version == INGREDIENT_IMAGE_PROMPT_VERSION


@pytest.mark.asyncio
async def test_generate_ingredient_image_downloads_a_hosted_url():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"choices": [{"message": {"images": [{"image_url": {"url": "https://cdn.example/basil.png"}}]}}]},
            )
        return httpx.Response(200, content=_PNG_1PX, headers={"content-type": "image/png"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
        ):
            result = await OpenRouterProvider().generate_ingredient_image("basilico")
    finally:
        await client.aclose()
    assert result.image_bytes == _PNG_1PX


@pytest.mark.asyncio
async def test_generate_ingredient_image_raises_when_no_image_comes_back():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "sorry, no image"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "INGREDIENT_IMAGE_MAX_RETRIES", 0),
        ):
            with pytest.raises(AIInvalidResponseError):
                await OpenRouterProvider().generate_ingredient_image("basilico")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_generate_ingredient_image_raises_after_exhausting_retries():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with (
            patch("app.ai.providers.openrouter.get_shared_client", return_value=client),
            patch.object(settings, "OPENROUTER_API_KEY", "test-key"),
            patch.object(settings, "INGREDIENT_IMAGE_MAX_RETRIES", 0),
        ):
            with pytest.raises(AIProviderError):
                await OpenRouterProvider().generate_ingredient_image("basilico")
    finally:
        await client.aclose()


# ---- service: cache + generation -------------------------------------------


def _result() -> IngredientImageResult:
    return IngredientImageResult(
        image_bytes=_PNG_1PX,
        content_type="image/png",
        model_name="recraft/recraft-v4.1-flash",
        prompt_version=INGREDIENT_IMAGE_PROMPT_VERSION,
        latency_ms=42,
        token_usage={"prompt_tokens": 5},
    )


@pytest.mark.asyncio
async def test_get_or_generate_reuses_a_cached_image_without_calling_the_model(db_session):
    canonical = canonicalize_food_name("Basilico")
    db_session.add(
        IngredientImage(
            canonical_key=canonical,
            display_name="Basilico",
            image_url="https://cdn.example/basilico.png",
            model_name="recraft/recraft-v4.1-flash",
            prompt_version=INGREDIENT_IMAGE_PROMPT_VERSION,
        )
    )
    await db_session.flush()

    provider = OpenRouterProvider()
    with patch.object(provider, "generate_ingredient_image", AsyncMock()) as generate:
        service = IngredientImageService(db_session, provider)
        url = await service.get_or_generate("basilico")  # different casing, same canonical key
    generate.assert_not_called()
    assert url == "https://cdn.example/basilico.png"


@pytest.mark.asyncio
async def test_get_or_generate_creates_and_uploads_on_a_cache_miss(db_session):
    user = User(firebase_uid="ing-user", email="ing@example.com", name="I")
    db_session.add(user)
    await db_session.flush()

    provider = OpenRouterProvider()
    uploaded = {"url": "https://cdn.example/uploads/abc.png", "storage": "local"}
    with (
        patch.object(provider, "generate_ingredient_image", AsyncMock(return_value=_result())),
        patch("app.ai.services.ingredient_image_service.save_generated_asset", AsyncMock(return_value=uploaded)) as save,
    ):
        service = IngredientImageService(db_session, provider)
        url = await service.get_or_generate("Basilico fresco", user_id=user.id)

    assert url == uploaded["url"]
    save.assert_awaited_once()
    key = save.await_args.args[2]
    assert key.startswith("uploads/ingredients/") and key.endswith(".png")

    row = await db_session.scalar(
        select(IngredientImage).where(IngredientImage.canonical_key == canonicalize_food_name("Basilico fresco"))
    )
    assert row is not None and row.image_url == uploaded["url"]

    log = await db_session.scalar(select(AIInferenceLog).where(AIInferenceLog.user_id == user.id))
    assert log.input_type == "ingredient_image"
    assert log.success is True
    assert log.model_name == "recraft/recraft-v4.1-flash"


@pytest.mark.asyncio
async def test_get_or_generate_regenerates_a_stale_prompt_version_in_place(db_session):
    canonical = canonicalize_food_name("basilico")
    stale = IngredientImage(
        canonical_key=canonical,
        display_name="basilico",
        image_url="https://cdn.example/old-style.png",
        model_name="recraft/recraft-v4.0",
        prompt_version="ingredient_image_v0",
    )
    db_session.add(stale)
    await db_session.flush()
    stale_id = stale.id

    provider = OpenRouterProvider()
    uploaded = {"url": "https://cdn.example/new-style.png", "storage": "local"}
    with (
        patch.object(provider, "generate_ingredient_image", AsyncMock(return_value=_result())),
        patch("app.ai.services.ingredient_image_service.save_generated_asset", AsyncMock(return_value=uploaded)),
    ):
        service = IngredientImageService(db_session, provider)
        url = await service.get_or_generate("basilico")

    assert url == uploaded["url"]
    refreshed = await db_session.get(IngredientImage, stale_id)
    assert refreshed.image_url == uploaded["url"]
    assert refreshed.prompt_version == INGREDIENT_IMAGE_PROMPT_VERSION
    # Same row is reused, not duplicated.
    log = await db_session.scalar(
        select(AIInferenceLog).where(AIInferenceLog.input_type == "ingredient_image")
    )
    assert log is not None


@pytest.mark.asyncio
async def test_get_or_generate_is_best_effort_on_model_failure(db_session):
    provider = OpenRouterProvider()
    with patch.object(provider, "generate_ingredient_image", AsyncMock(side_effect=RuntimeError("model down"))):
        service = IngredientImageService(db_session, provider)
        url = await service.get_or_generate("basilico")
    assert url is None

    log = await db_session.scalar(
        select(AIInferenceLog)
        .where(AIInferenceLog.input_type == "ingredient_image", AIInferenceLog.success.is_(False))
        .order_by(AIInferenceLog.id.desc())
    )
    assert log is not None and "model down" in log.error_message


@pytest.mark.asyncio
async def test_get_or_generate_falls_back_to_a_stale_image_on_failure(db_session):
    canonical = canonicalize_food_name("basilico")
    stale = IngredientImage(
        canonical_key=canonical,
        display_name="basilico",
        image_url="https://cdn.example/old-style.png",
        model_name="m",
        prompt_version="ingredient_image_v0",
    )
    db_session.add(stale)
    await db_session.flush()

    provider = OpenRouterProvider()
    with patch.object(provider, "generate_ingredient_image", AsyncMock(side_effect=RuntimeError("down"))):
        service = IngredientImageService(db_session, provider)
        url = await service.get_or_generate("basilico")
    assert url == "https://cdn.example/old-style.png"


@pytest.mark.asyncio
async def test_get_or_generate_returns_none_for_a_blank_name(db_session):
    service = IngredientImageService(db_session)
    assert await service.get_or_generate("   ") is None
    assert await service.get_or_generate("") is None


# ---- Celery task ------------------------------------------------------------


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_generate_for_meal_fills_every_item_without_an_image(db_session):
    from app.models.meal import Meal, MealItem
    from app.tasks import ingredient_images

    user = User(firebase_uid="task-user", email="task@example.com", name="T")
    db_session.add(user)
    await db_session.flush()
    meal = Meal(user_id=user.id, source_type="text", original_input="pizza", meal_name="Pizza", estimated_calories=500)
    db_session.add(meal)
    await db_session.flush()
    db_session.add(MealItem(meal_id=meal.id, name="Impasto", weight_grams=200, calories_per_100g=270))
    db_session.add(
        MealItem(
            meal_id=meal.id,
            name="Mozzarella",
            weight_grams=90,
            calories_per_100g=280,
            image_url="https://cdn.example/already-there.png",
        )
    )
    await db_session.flush()

    async def fake_get_or_generate(self, name, *, user_id=None):
        return f"https://cdn.example/{name.lower()}.png"

    with (
        patch.object(ingredient_images, "SessionLocal", lambda: _SessionCtx(db_session)),
        patch.object(IngredientImageService, "get_or_generate", fake_get_or_generate),
    ):
        await ingredient_images._generate_for_meal(meal.id, user.id)

    from sqlalchemy.orm import selectinload

    refreshed = await db_session.scalar(
        select(Meal).where(Meal.id == meal.id).options(selectinload(Meal.items)).execution_options(populate_existing=True)
    )
    by_name = {item.name: item.image_url for item in refreshed.items}
    assert by_name["Impasto"] == "https://cdn.example/impasto.png"
    # Already had one: never touched again, and never re-requested.
    assert by_name["Mozzarella"] == "https://cdn.example/already-there.png"


@pytest.mark.asyncio
async def test_generate_for_meal_one_bad_ingredient_never_stops_the_rest(db_session):
    from app.models.meal import Meal, MealItem
    from app.tasks import ingredient_images

    user = User(firebase_uid="task-user2", email="task2@example.com", name="T2")
    db_session.add(user)
    await db_session.flush()
    meal = Meal(user_id=user.id, source_type="text", original_input="bowl", meal_name="Bowl", estimated_calories=400)
    db_session.add(meal)
    await db_session.flush()
    db_session.add(MealItem(meal_id=meal.id, name="Riso", weight_grams=150, calories_per_100g=130))
    db_session.add(MealItem(meal_id=meal.id, name="Tofu", weight_grams=100, calories_per_100g=120))
    await db_session.flush()

    async def flaky(self, name, *, user_id=None):
        if name == "Riso":
            raise RuntimeError("boom")
        return "https://cdn.example/tofu.png"

    with (
        patch.object(ingredient_images, "SessionLocal", lambda: _SessionCtx(db_session)),
        patch.object(IngredientImageService, "get_or_generate", flaky),
    ):
        await ingredient_images._generate_for_meal(meal.id, user.id)

    from sqlalchemy.orm import selectinload

    refreshed = await db_session.scalar(
        select(Meal).where(Meal.id == meal.id).options(selectinload(Meal.items)).execution_options(populate_existing=True)
    )
    by_name = {item.name: item.image_url for item in refreshed.items}
    assert by_name["Riso"] is None
    assert by_name["Tofu"] == "https://cdn.example/tofu.png"


@pytest.mark.asyncio
async def test_task_tears_down_the_shared_client_and_engine_after_every_run(db_session):
    """Regression test (live-observed 2026-09-30): each Celery task invocation
    opens its own event loop via asyncio.run(). A shared httpx client or DB
    engine left bound to that loop breaks the *next* task to touch it with
    "Event loop is closed" — exactly what happened to the first ingredient of
    a real meal in staging. Both must be torn down every time, success or not."""
    from app.tasks import ingredient_images

    fake_engine = AsyncMock()
    with (
        patch.object(ingredient_images, "_generate_for_meal", AsyncMock()) as generate,
        patch("app.ai.providers.openrouter.close_shared_client", AsyncMock()) as close_client,
        patch.object(ingredient_images, "engine", fake_engine),
    ):
        await ingredient_images._generate_for_meal_with_cleanup(1, 2)
    generate.assert_awaited_once_with(1, 2)
    close_client.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()

    fake_engine = AsyncMock()
    with (
        patch.object(ingredient_images, "_generate_for_meal", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("app.ai.providers.openrouter.close_shared_client", AsyncMock()) as close_client,
        patch.object(ingredient_images, "engine", fake_engine),
    ):
        with pytest.raises(RuntimeError):
            await ingredient_images._generate_for_meal_with_cleanup(1, 2)
    close_client.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()


def test_enqueue_is_a_noop_when_disabled(monkeypatch):
    from app.tasks import ingredient_images

    monkeypatch.setattr(settings, "INGREDIENT_IMAGE_ENABLED", False)
    with patch.object(ingredient_images.generate_ingredient_images, "delay") as delay:
        ingredient_images.enqueue_ingredient_images(1, user_id=2)
    delay.assert_not_called()


def test_enqueue_never_raises_when_the_broker_is_unreachable(monkeypatch):
    from app.tasks import ingredient_images

    monkeypatch.setattr(settings, "INGREDIENT_IMAGE_ENABLED", True)
    with patch.object(ingredient_images.generate_ingredient_images, "delay", side_effect=RuntimeError("no broker")):
        ingredient_images.enqueue_ingredient_images(1, user_id=2)  # must not raise
