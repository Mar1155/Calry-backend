"""Amounts a person writes are facts the estimate must keep."""

import pytest

from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.ai.services.stated_quantities import apply_stated_quantities, extract_stated_quantities


def _grams(text: str) -> list[tuple[str, float]]:
    return [(q.food_tokens[0], q.grams) for q in extract_stated_quantities(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("250g di pasta al ragu", [("pasta", 250)]),
        ("250g di pasta pesata da cruda e ragu", [("pasta", 250)]),
        ("80g di riso con pollo", [("riso", 80)]),
        ("pasta 180 gr", [("pasta", 180)]),
        ("1,5 kg di patate, 200 ml di latte", [("patate", 1500), ("latte", 200)]),
        ("una manciata 50g di verdure grigliate", [("verdure", 50)]),
        ("2 barrette da 35g", [("barrette", 70)]),
        ("due barrette proteiche 35g l una con avena e frutta secca", [("barrette", 70)]),
        ("3 eggs, 30 g of cheese", [("cheese", 30)]),
        ("250 g pasta", [("pasta", 250)]),
        ("200 g Magerquark mit 100 g Erdbeeren", [("magerquark", 200), ("erdbeeren", 100)]),
        ("200g grilled chicken breast and 150g cooked rice", [("chicken", 200), ("rice", 150)]),
        ("yogurt greco 0% 170g con 15g di miele", [("yogurt", 170), ("miele", 15)]),
        ("100g de arroz con 150g de pollo", [("arroz", 100), ("pollo", 150)]),
    ],
)
def test_finds_the_amounts_a_person_wrote(text: str, expected: list[tuple[str, float]]) -> None:
    assert _grams(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "pasta e fagioli 300g",  # the dish, not the beans
        "una banana",
        "8 castagne",
        "cena da 600 kcal",
        "",
    ],
)
def test_ignores_ambiguous_or_missing_amounts(text: str) -> None:
    assert _grams(text) == []


def _result(*items: MealEstimateItem) -> MealEstimateResult:
    total = sum(i.estimated_calories for i in items)
    return MealEstimateResult(
        meal_name="Pasta al ragù",
        estimated_calories=total,
        estimated_min_calories=round(total * 0.85),
        estimated_max_calories=round(total * 1.2),
        confidence="medium",
        source_type="text",
        items=list(items),
        model_name="m",
        prompt_version="p",
    )


def _item(name: str, grams: int, density: float, carbs: float | None = None) -> MealEstimateItem:
    return MealEstimateItem(
        name=name,
        weight_grams=grams,
        calories_per_100g=density,
        carbs_g=carbs,
        estimated_calories=round(grams * density / 100),
    )


def test_puts_back_a_stated_amount_and_rescales_the_item() -> None:
    result = _result(_item("Pasta di semola secca", 160, 357, carbs=115.0), _item("Ragù", 120, 150))
    report = apply_stated_quantities("250g di pasta al ragu", result)

    pasta = result.items[0]
    assert pasta.weight_grams == 250
    assert pasta.estimated_calories == 892
    assert pasta.carbs_g == pytest.approx(179.7, abs=0.1)
    assert result.estimated_calories == 892 + 180
    assert result.estimated_min_calories <= result.estimated_calories <= result.estimated_max_calories
    assert result.stated_quantity_enforced
    assert report.enforced == ["pasta=250g:was_160g"]


def test_a_dry_amount_turned_into_cooked_pasta_gets_its_dry_density_back() -> None:
    # The original bug: 250 g of pasta became 160 g of cooked pasta.
    result = _result(_item("Pasta di semola cotta", 160, 150), _item("Ragù", 120, 150))
    apply_stated_quantities("250g di pasta al ragu", result)

    pasta = result.items[0]
    assert (pasta.weight_grams, pasta.calories_per_100g) == (250, 357)
    assert "cotta" not in pasta.name.lower()
    assert result.estimated_calories == 892 + 180


def test_stated_cooked_weight_stays_cooked() -> None:
    result = _result(_item("Pasta di semola cotta", 180, 150))
    report = apply_stated_quantities("200g di pasta cotta al pomodoro", result)

    assert (result.items[0].weight_grams, result.items[0].calories_per_100g) == (200, 150)
    assert report.enforced == ["pasta cotta=200g:was_180g"]


def test_per_piece_amounts_multiply() -> None:
    result = _result(_item("Barretta proteica", 35, 400))
    apply_stated_quantities("due barrette proteiche 35g l una", result)
    assert result.items[0].weight_grams == 70


def test_matching_amounts_are_left_alone() -> None:
    result = _result(_item("Pasta secca", 248, 357))
    report = apply_stated_quantities("250g di pasta", result)
    assert result.items[0].weight_grams == 248
    assert report.honored == ["pasta=250g"]
    assert not result.stated_quantity_enforced


def test_unmatched_or_shared_amounts_are_only_reported() -> None:
    result = _result(_item("Petto di pollo", 100, 165), _item("Brodo di pollo", 200, 15))
    report = apply_stated_quantities("150g di pollo", result)
    assert [i.weight_grams for i in result.items] == [100, 200]
    assert report.unresolved == ["pollo=150g:several_items"]

    other = _result(_item("Chicken breast", 100, 165))
    assert apply_stated_quantities("150g di pollo", other).unresolved == ["pollo=150g:no_item"]


def test_a_whole_dish_weight_never_lands_on_one_ingredient() -> None:
    # "torta di mele 150g": 150 g is the slice, not the apples inside it.
    cake = _result(_item("Farina", 40, 364), _item("Mele", 50, 52), _item("Burro", 20, 758))
    report = apply_stated_quantities("200g di torta di mele", cake)
    assert [i.weight_grams for i in cake.items] == [40, 50, 20]
    assert report.unresolved == ["torta di mele=200g:partial_match"]

    whole = _result(_item("Torta di mele", 120, 265))
    apply_stated_quantities("200g di torta di mele", whole)
    assert whole.items[0].weight_grams == 200


def test_clarification_results_are_untouched() -> None:
    result = _result(_item("Pasta", 100, 357))
    result.needs_clarification = True
    apply_stated_quantities("250g di pasta", result)
    assert result.items[0].weight_grams == 100


@pytest.mark.parametrize(("mode", "expected_grams"), [("shadow", 160), ("enforce", 250), ("off", 160)])
def test_lock_mode_controls_whether_the_estimate_changes(monkeypatch, mode: str, expected_grams: int) -> None:
    from app.ai.services.calorie_estimation_service import AICalorieEstimationService
    from app.core.config import settings

    monkeypatch.setattr(settings, "STATED_QUANTITY_LOCK", mode)
    result = _result(_item("Pasta secca", 160, 357))
    AICalorieEstimationService._finalize(  # type: ignore[call-arg]
        AICalorieEstimationService.__new__(AICalorieEstimationService),
        result,
        None,
        "text",
        stated_text="250g di pasta",
    )

    assert result.items[0].weight_grams == expected_grams
    if mode == "off":
        assert result.stated_quantity_report is None
    else:
        assert result.stated_quantity_report["mode"] == mode
        assert result.stated_quantity_report["enforced"] == ["pasta=250g:was_160g"]
