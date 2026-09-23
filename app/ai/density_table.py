"""Curated food density / typical-portion reference table.

Static data (NOT a vector DB, NOT an embedding store) used deterministically by
the validation layer to:
  1. fill a missing weight_grams from the typical portion of a recognised food,
  2. sanity-band an item's calories_per_100g against a plausible per-category
     range instead of relying only on the single global 900 kcal/100g ceiling.

Keyword matching is intentionally simple: we match whole canonical tokens of the
item name against each entry's keyword set. On no match the validator falls back
to its existing behaviour, so this table can only help, never regress.
"""
from dataclasses import dataclass

from app.core.text_normalization import canonicalize_food_name


@dataclass(frozen=True)
class FoodDensity:
    name: str
    kcal_per_100g_min: float
    kcal_per_100g_max: float
    typical_portion_g: int
    keywords: frozenset[str]


def _entry(name: str, lo: float, hi: float, portion: int, *keywords: str) -> FoodDensity:
    return FoodDensity(name, lo, hi, portion, frozenset(keywords))


# Ordered roughly by specificity; lookup returns the first keyword match.
# Keywords are bilingual (English + Italian) canonical singular tokens — the
# same form canonicalize_food_name() produces after singularizing a plural.
_TABLE: tuple[FoodDensity, ...] = (
    _entry("Olive / cooking oil", 800, 900, 14, "oil", "olive", "olio"),
    _entry("Butter", 700, 760, 12, "butter", "burro"),
    _entry(
        "Nuts", 550, 700, 30,
        "nut", "almond", "walnut", "peanut", "cashew", "pistachio",
        "mandorla", "noce", "nocciola", "pistacchio", "arachide", "anacardo",
    ),
    _entry(
        "Cheese", 280, 420, 40,
        "cheese", "cheddar", "mozzarella", "parmesan", "feta",
        "formaggio", "parmigiano", "grana", "ricotta",
    ),
    _entry("Chocolate", 480, 600, 30, "chocolate", "cioccolato", "cioccolata"),
    _entry("Pizza", 240, 300, 350, "pizza", "margherita"),
    _entry("Burger", 220, 290, 250, "burger", "cheeseburger", "hamburger"),
    _entry("Fries", 290, 360, 130, "fries", "chips", "patatina"),
    _entry("Bacon", 380, 540, 30, "bacon", "pancetta"),
    # Tomato sauce / passata: the common sugo/pizza-topping component, distinct
    # from the fresher, lower-density "cooked vegetables" tomato entry below.
    _entry("Tomato sauce", 40, 90, 150, "sugo", "salsa", "passata"),
    # Baked pizza/bread dough — the decomposed "impasto" component of a pizza.
    _entry("Baked dough / pizza base", 240, 280, 150, "dough", "impasto"),
    _entry(
        "Cooked pasta", 130, 200, 320,
        "pasta", "spaghetti", "penne", "lasagna", "lasagne",
    ),
    _entry("Cooked rice", 110, 170, 180, "rice", "risotto", "riso"),
    _entry("Bread", 240, 300, 40, "bread", "toast", "baguette", "roll", "pane"),
    _entry(
        "Croissant / pastry", 380, 470, 60,
        "croissant", "pastry", "donut", "doughnut", "cornetto", "brioche",
    ),
    _entry("Cake", 330, 450, 90, "cake", "brownie", "muffin", "torta"),
    _entry("Grilled chicken breast", 150, 200, 150, "chicken", "breast", "pollo"),
    _entry("Beef / steak", 200, 320, 180, "beef", "steak", "manzo", "bistecca"),
    _entry(
        "Salmon / oily fish", 180, 250, 150,
        "salmon", "mackerel", "tuna", "salmone", "sgombro", "tonno",
    ),
    _entry(
        "White fish", 90, 140, 150,
        "cod", "haddock", "tilapia", "fish", "pesce", "merluzzo", "nasello",
    ),
    _entry("Egg", 140, 165, 50, "egg", "uovo"),
    _entry(
        "Cooked legumes", 100, 160, 150,
        "beans", "lentil", "chickpea", "hummus", "fagiolo", "lenticchia", "cece",
    ),
    _entry("Potato", 80, 130, 180, "potato", "mashed", "patata"),
    _entry(
        "Leafy salad (undressed)", 15, 80, 120,
        "salad", "lettuce", "greens", "spinach", "insalata", "lattuga", "spinacio",
    ),
    _entry(
        "Cooked vegetables", 25, 90, 150,
        "vegetable", "broccoli", "carrot", "courgette", "zucchini", "pepper",
        "verdura", "broccolo", "carota", "zucchina", "peperone", "melanzana",
        "carciofo", "zucca", "fungo", "pomodoro", "cipolla",
    ),
    _entry(
        "Fruit", 30, 90, 130,
        "fruit", "apple", "banana", "orange", "berry", "berries", "grape",
        "frutta", "mela", "arancia", "pera", "pesca", "albicocca", "prugna",
        "ciliegia", "fragola", "anguria", "melone", "uva",
    ),
    _entry("Yogurt", 40, 110, 150, "yogurt", "yoghurt"),
    _entry("Milk", 30, 70, 250, "milk", "latte", "cappuccino"),
    _entry(
        "Soda / sugary drink", 30, 55, 330,
        "soda", "cola", "coke", "lemonade", "aranciata", "gassosa", "bibita",
    ),
    _entry("Beer", 35, 60, 330, "beer", "birra"),
    _entry("Wine", 70, 90, 150, "wine", "vino"),
    _entry("Soup", 30, 90, 300, "soup", "broth", "zuppa", "minestra", "minestrone", "brodo"),
    _entry("Cereal (dry)", 350, 420, 40, "cereal", "granola", "muesli", "cereale"),
    _entry("Oatmeal (cooked)", 60, 100, 240, "oat", "oatmeal", "porridge", "avena"),
)


def lookup_food(name: str | None) -> FoodDensity | None:
    """Return the first density entry whose keywords intersect the item name's
    canonical tokens, or None when nothing matches."""
    if not name:
        return None
    tokens = set(canonicalize_food_name(name).split(" "))
    if not tokens:
        return None
    for entry in _TABLE:
        if entry.keywords & tokens:
            return entry
    return None
