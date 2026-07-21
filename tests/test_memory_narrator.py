from app.memory import narrator


def test_discovery_portion_is_grounded_in_fact() -> None:
    text = narrator.render_moment(
        "discovery", "portion_model", {"display_name": "pasta", "grams": 120, "sample_count": 11, "span_days": 74}
    )
    assert "pasta" in text
    assert "120 g" in text


def test_learning_and_calibration_render() -> None:
    learning = narrator.render_moment("learning", "ai_calibration", {"scope": "overall", "no_edit_rate": 0.8, "sample_count": 20})
    calibration = narrator.render_moment("calibration", "ai_calibration", {"scope": "source:photo", "within_5pct_rate": 0.7, "sample_count": 20})
    assert "corrections" in learning
    assert "photo" in calibration
    assert "5%" in calibration


def test_evolution_mentions_old_and_new() -> None:
    text = narrator.render_moment("evolution", "portion_model", {"display_name": "rice", "old_grams": 100, "grams": 130})
    assert "100 g" in text
    assert "130 g" in text


def test_milestone_uses_count() -> None:
    text = narrator.render_moment("milestone", "relationship", {"milestone": "meals_confirmed", "count": 50})
    assert "50" in text


def test_unknown_locale_falls_back_to_english() -> None:
    fact = {"display_name": "pasta", "grams": 120}
    assert narrator.render_moment("discovery", "portion_model", fact, "xx") == narrator.render_moment(
        "discovery", "portion_model", fact, "en"
    )


def test_belief_statements_per_domain() -> None:
    assert "120 g" in narrator.render_belief_statement("portion_model", {"display_name": "pasta", "grams": 120})
    assert "favourite" in narrator.render_belief_statement("preference", {"display_name": "oats", "preference_type": "favourite"})
    assert "5%" in narrator.render_belief_statement("ai_calibration", {"scope": "overall"})


def test_narrator_is_template_only() -> None:
    assert narrator.MODEL_VERSION == "template"
    assert not hasattr(narrator, "OpenRouterProvider")
