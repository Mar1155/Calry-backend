"""Scoring for the meal-estimation eval. Pure functions: no network, no DB."""

from __future__ import annotations

import re
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Case:
    id: str
    input: str
    locale: str
    ref_kcal: float
    tolerance: float
    stated: dict[str, float] = field(default_factory=dict)
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    note: str | None = None
    dish_weight: float | None = None


@dataclass
class CaseResult:
    id: str
    kcal: int | None
    ref_kcal: float
    tolerance: float
    error_pct: float | None
    within_tolerance: bool
    stated_checked: int = 0
    stated_honored: int = 0
    stated_misses: list[str] = field(default_factory=list)
    category: str | None = None
    category_confidence: str | None = None
    category_expected: str | None = None
    category_ok: bool | None = None
    lock_enforced: list[str] = field(default_factory=list)
    lock_false_positive: bool = False
    items: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    error: str | None = None
    tags: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def _stem(word: str) -> str:
    word = word.casefold()
    return word[:5] if len(word) > 5 else word


def stated_weight(items: list[dict[str, Any]], food: str) -> int | None:
    """Total weight of the items whose name shares a word stem with ``food``."""
    stem = _stem(food)
    matched = [
        item for item in items if any(_stem(w) == stem for w in re.findall(r"[^\W\d_]+", str(item.get("name", ""))))
    ]
    if not matched:
        return None
    return sum(int(item.get("weight_grams") or 0) for item in matched)


def score_case(
    case: Case,
    *,
    kcal: int | None,
    items: list[dict[str, Any]],
    category: str | None = None,
    category_confidence: str | None = None,
    lock_enforced: list[str] | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> CaseResult:
    error_pct = None if kcal is None else (kcal - case.ref_kcal) / case.ref_kcal
    result = CaseResult(
        id=case.id,
        kcal=kcal,
        ref_kcal=case.ref_kcal,
        tolerance=case.tolerance,
        error_pct=None if error_pct is None else round(error_pct, 4),
        within_tolerance=error_pct is not None and abs(error_pct) <= case.tolerance,
        category=category,
        category_confidence=category_confidence,
        category_expected=case.category,
        lock_enforced=list(lock_enforced or []),
        items=[f"{i.get('name')} {i.get('weight_grams')}g@{i.get('calories_per_100g')}" for i in items],
        latency_ms=latency_ms,
        error=error,
        tags=list(case.tags),
    )
    for food, grams in case.stated.items():
        result.stated_checked += 1
        weight = stated_weight(items, food)
        if weight is not None and abs(weight - grams) <= 0.05 * grams:
            result.stated_honored += 1
        else:
            result.stated_misses.append(f"{food}: wrote {grams:g}g, got {weight}")
    # A whole-dish weight moved onto one of several ingredients.
    result.lock_false_positive = bool(case.dish_weight and result.lock_enforced and len(items) > 1)
    if case.category is not None:
        result.category_ok = category == case.category and category_confidence == "high"
    return result


def _pct(values: list[float]) -> float | None:
    return round(100 * statistics.mean(values), 1) if values else None


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    scored = [r for r in results if r.error_pct is not None]
    errors = [abs(r.error_pct) for r in scored]  # type: ignore[arg-type]
    stated_checked = sum(r.stated_checked for r in results)
    category = [r for r in results if r.category_ok is not None]
    latencies = sorted(r.latency_ms for r in results if r.latency_ms is not None)
    return {
        "cases": len(results),
        "failed_calls": sum(1 for r in results if r.error),
        "within_tolerance_pct": _pct([1.0 if r.within_tolerance else 0.0 for r in results]),
        "mean_abs_error_pct": _pct(errors),
        "median_abs_error_pct": round(100 * statistics.median(errors), 1) if errors else None,
        "bias_pct": _pct([r.error_pct for r in scored]),  # type: ignore[misc]
        "stated_honored_pct": (
            round(100 * sum(r.stated_honored for r in results) / stated_checked, 1) if stated_checked else None
        ),
        "category_ok_pct": _pct([1.0 if r.category_ok else 0.0 for r in category]),
        "lock_enforced_cases": sum(1 for r in results if r.lock_enforced),
        "lock_false_positives": sum(1 for r in results if r.lock_false_positive),
        "latency_p50_ms": latencies[len(latencies) // 2] if latencies else None,
        "latency_p90_ms": latencies[int(len(latencies) * 0.9)] if latencies else None,
    }


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Metric deltas and the cases whose pass/fail flipped."""
    deltas = {}
    for key, value in current["summary"].items():
        before = baseline["summary"].get(key)
        if isinstance(value, (int, float)) and isinstance(before, (int, float)):
            deltas[key] = round(value - before, 1)
    before_ok = {r["id"]: r["within_tolerance"] for r in baseline["results"]}
    fixed = [r["id"] for r in current["results"] if r["within_tolerance"] and before_ok.get(r["id"]) is False]
    broken = [r["id"] for r in current["results"] if not r["within_tolerance"] and before_ok.get(r["id"]) is True]
    return {"deltas": deltas, "now_passing": fixed, "now_failing": broken}
