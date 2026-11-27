"""The meal-estimation eval (evals/meal_estimation) stays loadable and its
scoring stays honest. The live run itself needs the model and is manual."""

from app.ai.services.stated_quantities import extract_stated_quantities
from evals.meal_estimation.run import load_cases
from evals.meal_estimation.scoring import compare, score_case, stated_weight, summarize


def test_cases_are_well_formed() -> None:
    cases = load_cases()
    assert len(cases) >= 40
    assert len({c.id for c in cases}) == len(cases)
    for case in cases:
        assert case.ref_kcal > 0 and 0 < case.tolerance <= 0.5, case.id
        assert case.category in (None, "breakfast", "lunch", "dinner", "snack"), case.id
        for food in case.stated:
            assert food.lower()[:5] in case.input.lower(), (case.id, food)


def test_every_stated_amount_in_the_set_is_found_by_the_lock() -> None:
    for case in load_cases():
        found = {round(q.grams) for q in extract_stated_quantities(case.input)}
        assert {round(g) for g in case.stated.values()} <= found, case.id


def test_scoring_reads_tolerance_stated_weights_and_category() -> None:
    case = next(c for c in load_cases() if c.id == "pasta_cooked_stated")
    items = [{"name": "Pasta di semola cotta", "weight_grams": 250, "calories_per_100g": 150}]
    good = score_case(case, kcal=430, items=items, category="dinner", category_confidence="high")
    bad = score_case(case, kcal=900, items=[{"name": "Pasta", "weight_grams": 100}], category="dinner")

    assert good.within_tolerance and good.stated_honored == 1 and good.category_ok
    assert not bad.within_tolerance and bad.stated_misses and bad.category_ok is False
    assert stated_weight(items, "pasta") == 250

    summary = summarize([good, bad])
    assert summary["within_tolerance_pct"] == 50.0
    assert summary["stated_honored_pct"] == 50.0

    before = {"summary": summary, "results": [good.as_json(), bad.as_json()]}
    bad_fixed = score_case(case, kcal=440, items=items, category="dinner", category_confidence="high")
    after = {"summary": summarize([good, bad_fixed]), "results": [good.as_json(), bad_fixed.as_json()]}
    diff = compare(after, before)
    assert diff["deltas"]["within_tolerance_pct"] == 50.0
