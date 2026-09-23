"""Italian-language support for the food-memory canonicalizer and the density
table (C22): most Calry users type in Italian, so both must recognise Italian
plurals/filler words, not just English ones."""
from app.ai.density_table import lookup_food
from app.core.text_normalization import canonicalize_food_name


def test_canonicalize_merges_italian_wording_variants():
    target = canonicalize_food_name("Pomodoro e mozzarella")
    assert canonicalize_food_name("mozzarella e pomodoro") == target
    assert canonicalize_food_name("un pomodoro con la mozzarella") == target


def test_canonicalize_merges_italian_plurals():
    assert canonicalize_food_name("2 uova") == canonicalize_food_name("2 uovo")
    assert canonicalize_food_name("patate fritte") == canonicalize_food_name("patata fritta") \
        or canonicalize_food_name("patate") == canonicalize_food_name("patata")


def test_canonicalize_does_not_corrupt_e_ending_singulars():
    # "carne"/"pane"/"latte" are already singular; a blind e->a suffix rule
    # would wrongly turn them into "carna"/"pana"/"latta". They must round-trip.
    for word in ("carne", "pane", "latte", "sale"):
        assert canonicalize_food_name(word) == word


def test_density_table_matches_italian_keywords():
    assert lookup_food("formaggio") is not None
    assert lookup_food("mozzarella") is not None
    assert lookup_food("olio") is not None
    assert lookup_food("pomodori") is not None  # plural -> singularized -> matched
    assert lookup_food("funghi") is not None


def test_density_table_decomposes_pizza_components():
    """The exact motivating example: pizza margherita decomposed into ingredients
    should each resolve to a sane density entry, not one whole-dish item."""
    assert lookup_food("impasto") is not None
    assert lookup_food("sugo") is not None
    assert lookup_food("mozzarella") is not None
    # The sauce and the fresh-vegetable tomato entry stay distinct.
    sauce = lookup_food("sugo")
    veg = lookup_food("pomodoro")
    assert sauce is not veg
