"""Food region detection (C28): a separate, cheap vision call that pins visible
ingredients on the photo without touching the calorie estimate."""
import asyncio
import base64
import json
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas.food_detection import FoodDetectionResult
from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.ai.services.food_detection_service import DetectionOutcome, FoodDetectionService
from app.ai.streaming import protocol
from app.api.v1.routes.meals import _process_and_save_meal, _serialize_meal
from app.models.inference import AIInferenceLog
from app.models.user import User
from app.services import stream_bus

# ---- parsing -----------------------------------------------------------------


def test_parse_detections_normalises_native_box_2d():
    raw = json.dumps(
        {
            "detections": [
                {"label": "prosciutto", "box_2d": [100, 200, 300, 450]},
                {"label": "  basilico  ", "box_2d": [700, 600, 500, 400]},  # inverted
            ]
        }
    )
    regions = OpenRouterProvider._parse_detections(raw, 8)
    assert [r.label for r in regions] == ["prosciutto", "basilico"]
    first = regions[0]
    assert (first.ymin, first.xmin, first.ymax, first.xmax) == (0.1, 0.2, 0.3, 0.45)
    second = regions[1]
    assert second.ymin < second.ymax and second.xmin < second.xmax


def test_parse_detections_drops_noise_and_caps_output():
    raw = "```json\n" + json.dumps(
        {
            "detections": [
                {"label": "whole plate", "box_2d": [0, 0, 1000, 1000]},  # no location info
                {"label": "speck", "box_2d": [10, 10, 12, 12]},  # degenerate
                {"label": "", "box_2d": [100, 100, 200, 200]},
                {"label": "olive", "box_2d": [100, 100]},
                {"label": "olive", "box_2d": [100, 100, 200, 200]},
                {"label": "Olive", "box_2d": [300, 300, 400, 400]},
                {"label": "olive", "box_2d": [500, 500, 600, 600]},  # third box for one label
                {"label": "tomato", "box_2d": ["x", 1, 2, 3]},
                {"label": "rucola", "box_2d": [600, 100, 800, 300]},
            ]
        }
    ) + "\n```"
    regions = OpenRouterProvider._parse_detections(raw, 3)
    assert [r.label for r in regions] == ["olive", "Olive", "rucola"]


@pytest.mark.parametrize("raw", ["", "not json", '{"detections": "nope"}', "[1, 2]"])
def test_parse_detections_never_raises(raw):
    assert OpenRouterProvider._parse_detections(raw, 8) == []


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


# ---- service -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_is_best_effort():
    provider = OpenRouterProvider()
    with patch.object(provider, "detect_food_regions", AsyncMock(side_effect=RuntimeError("provider down"))):
        outcome = await FoodDetectionService(provider).detect("https://example.com/a.jpg", "it")
    assert outcome.result is None
    assert outcome.regions == []
    assert "provider down" in outcome.error


@pytest.mark.asyncio
async def test_join_abandons_slow_detection_without_delaying_the_meal():
    async def slow() -> DetectionOutcome:
        await asyncio.sleep(10)
        return DetectionOutcome(image_url="x")

    task = asyncio.create_task(slow())
    assert await FoodDetectionService.join(task, 0.01) is None
    await asyncio.sleep(0)
    assert task.cancelled()
    assert await FoodDetectionService.join(None, 1) is None


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
    await stream_bus.publish("job1", protocol.regions([{"label": "basilico", "ymin": 0.1, "xmin": 0.1, "ymax": 0.2, "xmax": 0.2}]))
    await stream_bus.publish("job1", protocol.item(1, {"name": "mozzarella"}))

    state = await stream_bus.read_state("job1")
    assert [it["index"] for it in state["items"]] == [0, 1]
    assert state["regions"]["regions"][0]["label"] == "basilico"


@pytest.mark.asyncio
async def test_detection_outcome_is_logged_and_persisted_with_the_meal(db_session):
    user = User(firebase_uid="detect-user", email="detect@example.com", name="D")
    db_session.add(user)
    await db_session.flush()

    detection = FoodDetectionResult(
        regions=[],
        model_name="google/gemini-2.5-flash-lite",
        prompt_version="food_detection_v1",
        raw_output=json.dumps({"detections": [{"label": "basilico", "box_2d": [100, 100, 300, 300]}]}),
        latency_ms=900,
    )
    detection.regions = OpenRouterProvider._parse_detections(detection.raw_output, 8)
    outcome = DetectionOutcome(image_url="https://example.com/a.jpg", result=detection)
    log_id = await FoodDetectionService.log(db_session, user.id, outcome)

    estimation = MealEstimateResult(
        meal_name="Pizza",
        estimated_calories=540,
        confidence="medium",
        source_type="photo",
        items=[MealEstimateItem(name="Impasto", weight_grams=200, calories_per_100g=270.0)],
        model_name="m",
        prompt_version="p",
        linked_inference_log_ids=[log_id],
    )
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
            estimation=estimation,
            detected_regions=outcome.regions_payload(),
        )

    serialized = _serialize_meal(meal)
    assert serialized["detected_regions"][0]["label"] == "basilico"
    assert serialized["estimated_calories"] == 540  # estimate untouched by detection

    log = await db_session.get(AIInferenceLog, log_id)
    assert log.input_type == "photo_detection"
    assert log.item_count == 1
    assert log.meal_id == meal.id


# ---- photo worker ------------------------------------------------------------


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_photo_worker_runs_detection_in_parallel_and_persists_pins(db_session):
    import uuid

    from app.models.meal import Meal
    from app.models.meal_analysis import MealAnalysisJob
    from app.tasks import meal_analysis

    user = User(firebase_uid=f"worker-{uuid.uuid4().hex[:8]}", email=f"w-{uuid.uuid4().hex[:8]}@x.io", name="W")
    db_session.add(user)
    await db_session.flush()
    job = MealAnalysisJob(user_id=user.id, image_url="https://example.com/pizza.jpg", locale="it")
    db_session.add(job)
    await db_session.flush()

    estimation = MealEstimateResult(
        meal_name="Pizza",
        estimated_calories=540,
        confidence="medium",
        source_type="photo",
        items=[MealEstimateItem(name="Impasto", weight_grams=200, calories_per_100g=270.0)],
        model_name="m",
        prompt_version="p",
    )
    estimate_started = asyncio.Event()
    detection_seen_estimate_running = []

    async def fake_stream(*args, **kwargs):
        estimate_started.set()
        await asyncio.sleep(0.05)  # detection finishes while the estimate is still running
        yield protocol.item(0, {"name": "Impasto"})
        yield {"type": "__complete__", "result": estimation}

    async def fake_detect(self, image_url, locale=None):
        await estimate_started.wait()
        detection_seen_estimate_running.append(locale)
        result = FoodDetectionResult(
            regions=OpenRouterProvider._parse_detections(
                json.dumps({"detections": [{"label": "basilico", "box_2d": [100, 100, 300, 300]}]}), 8
            ),
            model_name="google/gemini-2.5-flash-lite",
            prompt_version="food_detection_v1",
            raw_output="{}",
            latency_ms=5,
        )
        return DetectionOutcome(image_url=image_url, result=result)

    published: list[dict] = []

    async def fake_publish(job_id, event):
        published.append(event)

    with (
        patch.object(meal_analysis, "SessionLocal", lambda: _SessionCtx(db_session)),
        patch.object(stream_bus, "publish", fake_publish),
        patch("app.api.v1.routes.meals._build_user_context", AsyncMock(return_value=None)),
        patch("app.api.v1.routes.meals.InsightVersionService") as versions,
        patch("app.tasks.memory.enqueue_memory_distillation"),
        patch.object(meal_analysis.AICalorieEstimationService, "stream_estimate_from_image", fake_stream),
        patch.object(FoodDetectionService, "detect", fake_detect),
    ):
        versions.return_value.record = AsyncMock()
        meal_id = await meal_analysis._run_photo_analysis(job.id)

    types = [event["type"] for event in published]
    assert "regions" in types and types[-1] == "done"
    assert types.index("regions") < types.index("done")
    assert detection_seen_estimate_running == ["it"]

    meal = await db_session.get(Meal, meal_id)
    assert meal.detected_regions[0]["label"] == "basilico"
    assert meal.estimated_calories == 540
    done = published[-1]["meal"]
    assert done["detected_regions"][0]["label"] == "basilico"

