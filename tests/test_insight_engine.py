import datetime as dt
import json
from types import SimpleNamespace

import pytest

from app.ai.providers.openrouter import OpenRouterProvider
from app.core.config import settings
from app.insights.detectors import PatternDetector
from app.insights.engine import InsightEngine
from app.insights.features import FeatureExtractor
from app.insights.patterns import VerifiedPattern
from app.insights.ranking import PatternRanker


def _summary(date: dt.date, calories: int, *, goal: int = 2000, water: int = 0, burned: int = 0):
    return SimpleNamespace(
        date=date,
        consumed_calories=calories,
        burned_calories=burned,
        remaining_calories=goal - calories + burned,
        water_glasses=water,
    )


def _meal(date: dt.date, *, category: str = "lunch", correction: float | None = None):
    return SimpleNamespace(
        created_at=dt.datetime.combine(date, dt.time(12), tzinfo=dt.UTC),
        estimated_calories=500,
        meal_category=category,
        total_protein_g=30.0,
        total_carbs_g=45.0,
        total_fat_g=15.0,
        confirmed_calories=500 if correction is not None else None,
        correction_percent=correction,
        source_type="text",
    )


def test_feature_extraction_and_engine_are_deterministic() -> None:
    today = dt.date(2026, 7, 20)
    dates = [today - dt.timedelta(days=offset) for offset in range(7)]
    summaries = [
        _summary(date, calories, water=6 + index % 2, burned=200 if index < 3 else 0)
        for index, (date, calories) in enumerate(
            zip(dates, [2000, 1900, 2100, 1800, 2200, 2000, 1950], strict=True)
        )
    ]
    meals = [_meal(date, category=("lunch" if index < 5 else "dinner")) for index, date in enumerate(dates)]

    snapshot = FeatureExtractor.extract(
        period_days=7,
        end_date=today,
        calorie_goal=2000,
        summaries=summaries,
        meals=meals,
    )
    first = InsightEngine().generate(snapshot, limit=4)
    second = InsightEngine().generate(snapshot, limit=4)

    assert snapshot.average_calories == 1993
    assert snapshot.calorie_variance > 0
    assert snapshot.longest_logging_streak == 7
    assert [pattern.verified_dict() for pattern in first] == [pattern.verified_dict() for pattern in second]
    assert all(set(pattern.verified_dict()) == {"id", "category", "confidence", "priority", "payload"} for pattern in first)
    assert len(PatternDetector.registry) == 12


def test_no_tracking_data_produces_no_patterns() -> None:
    snapshot = FeatureExtractor.extract(
        period_days=30,
        end_date=dt.date(2026, 7, 20),
        calorie_goal=2000,
        summaries=[],
        meals=[],
    )

    assert InsightEngine().generate(snapshot, limit=4) == []


def test_ranker_uses_confidence_priority_novelty_and_deduplicates() -> None:
    def pattern(identifier: str, confidence: float, priority: int, novelty: float, concept: str):
        return VerifiedPattern(
            id=identifier,
            category="test",
            confidence=confidence,
            priority=priority,
            payload={"sample_size": 10},
            novelty=novelty,
            concept=concept,
        )

    ranked = PatternRanker().rank(
        [
            pattern("low", 0.8, 99, 0.99, "low"),
            pattern("same_a", 0.9, 80, 0.5, "same"),
            pattern("same_b", 0.9, 70, 0.9, "same"),
            pattern("novel", 0.9, 80, 0.8, "novel"),
        ],
        limit=4,
    )
    assert [item.id for item in ranked] == ["same_b", "low", "novel"]


@pytest.mark.asyncio
async def test_llm_receives_verified_patterns_only(monkeypatch) -> None:
    pattern = VerifiedPattern(
        id="goal_consistency",
        category="consistency",
        confidence=0.92,
        priority=84,
        payload={"days_logged": 7, "days_within_target": 6, "adherence_rate": 0.857},
    )
    captured: dict = {}

    async def fake_post(*, model, system_prompt, messages, response_format=None):
        captured["system_prompt"] = system_prompt
        captured["body"] = json.loads(messages[0]["content"])
        return (
            json.dumps(
                {
                    "patterns": [
                        {
                            "title": "Steady goal alignment",
                            "message": "Six logged days were within target.",
                            "confidence": "low",
                            "category": "invented",
                            "metric": "invented",
                            "evidence": ["invented"],
                        }
                    ]
                }
            ),
            1,
            None,
        )

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    provider = OpenRouterProvider()
    monkeypatch.setattr(provider, "_post_openrouter", fake_post)
    insights = await provider.generate_pattern_insights([pattern])

    assert captured["body"] == {
        "output_language": "English",
        "verified_patterns": [pattern.verified_dict()],
    }
    assert "raw meals" not in json.dumps(captured["body"]).lower()
    assert insights[0].category == "consistency"
    assert insights[0].confidence == "high"
    assert insights[0].metric == "86% within target"
    assert insights[0].evidence != ["invented"]
    assert all("_" not in item for item in insights[0].evidence)


def test_evidence_is_human_readable_and_localized() -> None:
    pattern = VerifiedPattern(
        id="ai_estimation_accuracy",
        category="ai_accuracy",
        confidence=0.94,
        priority=80,
        payload={
            "confirmed_meals": 91,
            "average_absolute_correction_percent": 2.6,
            "median_absolute_correction_percent": 0.0,
            "average_signed_correction_percent": -1.8,
            "estimates_within_ten_percent": 84,
            "accuracy_rate_within_ten_percent": 0.923,
            "correction_source_counts": {"photo": 60, "text": 31},
        },
    )

    insight = OpenRouterProvider._fallback_insight(pattern, locale="it-IT")

    assert insight.title == "Precisione delle stime"
    assert insight.metric == "92% entro ±10%"
    assert "91 pasti confermati analizzati" in insight.evidence
    assert "Fonte più confermata: foto" in insight.evidence
    assert all("_" not in item for item in insight.evidence)
