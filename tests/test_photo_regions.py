"""Photo pins: the photo estimate returns a box_2d per item, so every pin on
the photo is labelled with an ingredient the estimate actually counted
(replaces the separate C28 detection model, whose labels drifted)."""
import base64
import json
import uuid
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.ai.prompts.image_estimation import IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT
from app.ai.prompts.meal_estimation import TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.food_detection import MAX_REGIONS, region_from_box, regions_from_items
from app.ai.schemas.meal_estimate import (
    IMAGE_MEAL_ESTIMATE_RESPONSE_SCHEMA,
    MEAL_ESTIMATE_RESPONSE_SCHEMA,
    MealEstimateItem,
    MealEstimateResult,
)
from app.ai.streaming import protocol
from app.ai.streaming.partial_parser import StreamingMealParser
from app.api.v1.routes.meals import _process_and_save_meal, _serialize_meal
from app.models.user import User
from app.services import stream_bus

# ---- box sanitising ------------------------------------------------------------


def test_region_from_box_normalises_native_box_2d():
    region = region_from_box("  prosciutto ", [100, 200, 300, 450])
    assert region.label == "prosciutto"
    assert (region.ymin, region.xmin, region.ymax, region.xmax) == (0.1, 0.2, 0.3, 0.45)
    inverted = region_from_box("basilico", [700, 600, 500, 400])
    assert inverted.ymin < inverted.ymax and inverted.xmin < inverted.xmax


@pytest.mark.parametrize(
    "label, box",
    [
        ("whole plate", [0, 0, 1000, 1000]),  # no location info
        ("speck", [10, 10, 12, 12]),  # degenerate
        ("", [100, 100, 200, 200]),
        ("olive", [100, 100]),
        ("tomato", ["x", 1, 2, 3]),
        ("tomato", [True, 1, 200, 300]),
        ("olio", None),
        ("olio", "nope"),
    ],
)
def test_region_from_box_drops_unusable_boxes(label, box):
    assert region_from_box(label, box) is None


def test_regions_from_items_labels_pins_with_item_names_and_caps():
    items = [MealEstimateItem(name="Olio", box_2d=None)] + [
        MealEstimateItem(name=f"item {i}", box_2d=[100, 100, 300, 300]) for i in range(MAX_REGIONS + 2)
    ]
    regions = regions_from_items(items)
    assert len(regions) == MAX_REGIONS
    assert [r.label for r in regions] == [f"item {i}" for i in range(MAX_REGIONS)]
    assert regions_from_items([{"name": "mozzarella", "box_2d": [100, 100, 400, 400]}])[0].label == "mozzarella"


# ---- contract ---------------------------------------------------------------------


def test_only_the_photo_contract_asks_for_item_boxes():
    image_item = IMAGE_MEAL_ESTIMATE_RESPONSE_SCHEMA["properties"]["items"]["items"]
    text_item = MEAL_ESTIMATE_RESPONSE_SCHEMA["properties"]["items"]["items"]
    assert "box_2d" in image_item["properties"] and "box_2d" in image_item["required"]
    assert "box_2d" not in text_item["properties"] and "box_2d" not in text_item["required"]
    assert '"box_2d": [ymin, xmin, ymax, xmax] | null' in IMAGE_MEAL_ESTIMATION_SYSTEM_PROMPT
    assert "box_2d" not in TEXT_MEAL_ESTIMATION_SYSTEM_PROMPT


def test_dict_to_items_keeps_a_well_formed_box_only():
    items = OpenRouterProvider._dict_to_items(
        {
            "items": [
                {"name": "mozzarella", "box_2d": [100, 200, 300, 400]},
                {"name": "olio", "box_2d": None},
                {"name": "basilico", "box_2d": [1, 2, "x", 4]},
                {"name": "pane", "box_2d": [1, 2, 3]},
            ]
        }
    )
    assert [it.box_2d for it in items] == [[100.0, 200.0, 300.0, 400.0], None, None, None]


def test_streaming_preview_carries_the_box_through():
    payload = json.dumps(
        {"meal_name": "Pizza", "items": [{"name": "mozzarella", "weight_grams": 80, "box_2d": [1, 2, 300, 400]}]}
    )
    events = StreamingMealParser().feed(payload)
    item = next(value for kind, value in events if kind == "item")
    assert item["box_2d"] == [1, 2, 300, 400]


# ---- image orientation --------------------------------------------------------


def test_image_prep_bakes_exif_orientation_into_pixels():
    img = Image.new("RGB", (400, 200), "white")
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90° CW on display
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif)

    data_uri = OpenRouterProvider()._prepare_image_data_uri(buf.getvalue(), "image/jpeg")
    decoded = Image.open(BytesIO(base64.b64decode(data_uri.split(",", 1)[1])))
    # The model must see the photo the way the phone shows it: portrait.
    assert decoded.size == (200, 400)


# ---- stream state -------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1


@pytest.mark.asyncio
async def test_regions_are_snapshotted_apart_from_items(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(stream_bus, "get_redis", lambda: fake)
    await stream_bus.publish("job1", protocol.item(0, {"name": "impasto"}))
    await stream_bus.publish("job1", protocol.regions([{"label": "impasto", "ymin": 0.1, "xmin": 0.1, "ymax": 0.2, "xmax": 0.2}]))
    await stream_bus.publish("job1", protocol.item(1, {"name": "mozzarella"}))

    state = await stream_bus.read_state("job1")
    assert [it["index"] for it in state["items"]] == [0, 1]
    assert state["regions"]["regions"][0]["label"] == "impasto"


# ---- persistence ----------------------------------------------------------------


def _estimation() -> MealEstimateResult:
    return MealEstimateResult(
        meal_name="Pizza",
        estimated_calories=620,
        confidence="medium",
        source_type="photo",
        items=[
            MealEstimateItem(name="Impasto", weight_grams=200, calories_per_100g=270.0, box_2d=[100, 100, 900, 900]),
            MealEstimateItem(name="Mozzarella", weight_grams=30, calories_per_100g=266.0, box_2d=[300, 300, 500, 500]),
            MealEstimateItem(name="Olio", weight_grams=0, calories_per_100g=0.0),
        ],
        model_name="m",
        prompt_version="p",
    )


@pytest.mark.asyncio
async def test_saved_photo_meal_pins_match_its_items(db_session):
    user = User(firebase_uid=f"pins-{uuid.uuid4().hex[:8]}", email=f"p-{uuid.uuid4().hex[:8]}@x.io", name="P")
    db_session.add(user)
    await db_session.flush()

    with patch("app.api.v1.routes.meals.InsightVersionService") as versions, patch(
        "app.tasks.memory.enqueue_memory_distillation"
    ):
        versions.return_value.record = AsyncMock()
        meal = await _process_and_save_meal(
            db=db_session,
            user=user,
            source_type="photo",
            original_input="Pizza",
            image_url="https://example.com/a.jpg",
            audio_url=None,
            estimation=_estimation(),
        )

    serialized = _serialize_meal(meal)
    labels = [r["label"] for r in serialized["detected_regions"]]
    assert labels == ["Impasto", "Mozzarella"]
    assert set(labels) <= {item["name"] for item in serialized["items"]}


# ---- photo worker ------------------------------------------------------------


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_photo_worker_streams_pins_from_item_boxes(db_session):
    from app.models.meal import Meal
    from app.models.meal_analysis import MealAnalysisJob
    from app.tasks import meal_analysis

    user = User(firebase_uid=f"worker-{uuid.uuid4().hex[:8]}", email=f"w-{uuid.uuid4().hex[:8]}@x.io", name="W")
    db_session.add(user)
    await db_session.flush()
    job = MealAnalysisJob(user_id=user.id, image_url="https://example.com/pizza.jpg", locale="it")
    db_session.add(job)
    await db_session.flush()

    estimation = _estimation()

    async def fake_stream(*args, **kwargs):
        yield protocol.item(0, {"name": "Impasto", "box_2d": [100, 100, 900, 900]})
        yield protocol.item(1, {"name": "Olio"})
        yield protocol.item(2, {"name": "Mozzarella", "box_2d": [300, 300, 500, 500]})
        yield {"type": "__complete__", "result": estimation}

    published: list[dict] = []

    async def fake_publish(job_id, event):
        published.append(json.loads(json.dumps(event)))  # snapshot: the worker reuses its list

    with (
        patch.object(meal_analysis, "SessionLocal", lambda: _SessionCtx(db_session)),
        patch.object(stream_bus, "publish", fake_publish),
        patch("app.api.v1.routes.meals._build_user_context", AsyncMock(return_value=None)),
        patch("app.api.v1.routes.meals.InsightVersionService") as versions,
        patch("app.tasks.memory.enqueue_memory_distillation"),
        patch.object(meal_analysis.AICalorieEstimationService, "stream_estimate_from_image", fake_stream),
    ):
        versions.return_value.record = AsyncMock()
        meal_id = await meal_analysis._run_photo_analysis(job.id)

    types = [event["type"] for event in published]
    assert types[-1] == "done"
    # Each pin follows the item it belongs to, cumulatively.
    assert types[1:6] == ["item", "regions", "item", "item", "regions"]
    region_events = [e["regions"] for e in published if e["type"] == "regions"]
    assert [[r["label"] for r in regions] for regions in region_events] == [["Impasto"], ["Impasto", "Mozzarella"]]
    # The box is internal: previews reach the client in the usual shape.
    assert all("box_2d" not in e["item"] for e in published if e["type"] == "item")

    meal = await db_session.get(Meal, meal_id)
    assert [r["label"] for r in meal.detected_regions] == ["Impasto", "Mozzarella"]
    assert [r["label"] for r in published[-1]["meal"]["detected_regions"]] == ["Impasto", "Mozzarella"]
