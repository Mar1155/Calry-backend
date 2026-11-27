"""Run the meal-estimation eval against the live model.

Uses the production text pipeline (prompt, provider, validation, stated-quantity
lock) without a database: no user data is read and nothing is persisted, so it
is safe to run from a laptop. Needs OPENROUTER_API_KEY (for example through
``railway run -s "Calory Backend" -- ...``).

    python -m evals.meal_estimation.run
    python -m evals.meal_estimation.run --no-lock --label no-lock
    python -m evals.meal_estimation.run --repeat 3 --compare evals/results/<file>.json
    python -m evals.meal_estimation.run --only pasta_ragu_dry_250,barrette_luna

Each run writes evals/results/<date>_<prompt>_<label>.json with every case,
the model's items, and a summary; --compare prints the deltas against an
earlier run.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import statistics
import time
from pathlib import Path

from evals.meal_estimation.scoring import Case, CaseResult, compare, score_case, summarize

HERE = Path(__file__).parent
RESULTS = HERE.parent / "results"


def load_cases(only: set[str] | None = None) -> list[Case]:
    data = json.loads((HERE / "cases.json").read_text())
    cases = [Case(**raw) for raw in data["cases"]]
    return [c for c in cases if not only or c.id in only]


async def _estimate(case: Case, *, lock: bool) -> CaseResult:
    from app.ai.providers.openrouter import OpenRouterProvider
    from app.ai.schemas.meal_estimate import UserContext
    from app.ai.services.stated_quantities import apply_stated_quantities
    from app.ai.services.validation_service import AIValidationService

    started = time.perf_counter()
    try:
        raw = await OpenRouterProvider().estimate_meal_from_text(
            case.input, user_context=UserContext(locale=case.locale)
        )
        result = AIValidationService.validate_and_normalize_estimate(raw)
        report = apply_stated_quantities(case.input, result) if lock else None
    except Exception as exc:  # noqa: BLE001 - a failed call is a scored outcome
        return score_case(case, kcal=None, items=[], error=f"{type(exc).__name__}: {exc}"[:300])
    return score_case(
        case,
        kcal=result.estimated_calories,
        items=[i.model_dump() for i in result.items],
        category=result.meal_category_suggestion,
        category_confidence=result.meal_category_confidence,
        lock_enforced=report.enforced if report else [],
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


async def run(cases: list[Case], *, lock: bool, repeat: int, concurrency: int) -> list[CaseResult]:
    gate = asyncio.Semaphore(concurrency)

    async def one(case: Case) -> CaseResult:
        async with gate:
            return await _estimate(case, lock=lock)

    runs = await asyncio.gather(*(one(case) for case in cases for _ in range(repeat)))
    if repeat == 1:
        return list(runs)
    # Repeated runs: keep the median-kcal run per case and record the spread.
    by_case: dict[str, list[CaseResult]] = {}
    for result in runs:
        by_case.setdefault(result.id, []).append(result)
    merged = []
    for case in cases:
        group = [r for r in by_case[case.id] if r.kcal is not None] or by_case[case.id]
        group.sort(key=lambda r: r.kcal or 0)
        chosen = group[len(group) // 2]
        kcals = [r.kcal for r in group if r.kcal is not None]
        if len(kcals) > 1:
            chosen.items.append(f"spread: {min(kcals)}-{max(kcals)} kcal, sd {statistics.pstdev(kcals):.0f}")
        merged.append(chosen)
    return merged


def _print(results: list[CaseResult], summary: dict, comparison: dict | None) -> None:
    for r in results:
        mark = "ok " if r.within_tolerance else "OFF"
        err = "  n/a" if r.error_pct is None else f"{100 * r.error_pct:+5.0f}%"
        extra = []
        if r.stated_misses:
            extra.append("stated: " + "; ".join(r.stated_misses))
        if r.lock_enforced:
            extra.append(("LOCK FALSE POSITIVE: " if r.lock_false_positive else "lock: ") + ", ".join(r.lock_enforced))
        if r.category_ok is False:
            extra.append(f"category {r.category}/{r.category_confidence} != {r.category_expected}")
        if r.error:
            extra.append(r.error)
        print(f"{mark} {r.id:28} {r.kcal!s:>5} vs {r.ref_kcal:>5.0f} {err}  {' | '.join(extra)}")
    print("\nSUMMARY", json.dumps(summary, indent=1))
    if comparison:
        print("\nVS BASELINE", json.dumps(comparison, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-lock", action="store_true", help="skip the stated-quantity lock")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case (median kept, spread reported)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--label", default="", help="suffix for the results file")
    parser.add_argument("--compare", default="", help="earlier results file to diff against")
    args = parser.parse_args()

    from app.ai.prompts.meal_estimation import TEXT_MEAL_ESTIMATION_PROMPT_VERSION
    from app.core.config import settings

    cases = load_cases({c for c in args.only.split(",") if c} or None)
    results = asyncio.run(run(cases, lock=not args.no_lock, repeat=args.repeat, concurrency=args.concurrency))
    summary = summarize(results)
    payload = {
        "ran_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "model": settings.OPENROUTER_TEXT_MODEL,
        "prompt_version": TEXT_MEAL_ESTIMATION_PROMPT_VERSION,
        "lock": not args.no_lock,
        "repeat": args.repeat,
        "summary": summary,
        "results": [r.as_json() for r in results],
    }
    comparison = compare(payload, json.loads(Path(args.compare).read_text())) if args.compare else None
    RESULTS.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    label = f"_{args.label}" if args.label else ""
    path = RESULTS / f"{stamp}_{TEXT_MEAL_ESTIMATION_PROMPT_VERSION}{label}.json"
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    _print(results, summary, comparison)
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
