"""Deterministic food-name canonicalization.

One shared normalizer used by BOTH the food-memory upsert and the pre-inference
cache lookup, so the key a user types maps to the same key that was stored when
they previously confirmed the meal. Pure-python, no dependencies, fully
deterministic — unit-tested against a fixture set.
"""
import re
import unicodedata

# Low-information tokens that should not affect the cache key. Kept conservative:
# only words that never change which food is being described.
_FILLER_TOKENS = {
    "a", "an", "the", "of", "with", "and", "some", "my", "your", "for",
    "plate", "bowl", "cup", "glass", "serving", "portion", "piece", "pieces",
    "fresh", "homemade", "small", "medium", "large", "plain",
    # Italian equivalents (articles, prepositions, units, common adjectives).
    "un", "uno", "una", "il", "lo", "la", "i", "gli", "le", "l",
    "di", "del", "dello", "della", "dei", "degli", "delle",
    "con", "e", "ed", "al", "alla", "ai", "agli", "alle",
    "da", "in", "su", "per", "tra", "fra",
    "piatto", "ciotola", "tazza", "bicchiere", "porzione", "pezzo", "pezzi",
    "fresco", "fresca", "freschi", "fresche", "casalingo", "casalinga",
    "casalinghi", "casalinghe", "piccolo", "piccola", "piccoli", "piccole",
    "medio", "media", "medi", "medie", "grande", "grandi", "semplice",
}

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"\d")

# Curated Italian plural -> singular map for common foods. Italian pluralization
# is too irregular to safely handle with a suffix rule the way English is below
# (e.g. blindly turning a trailing "e" into "a" would corrupt singular words
# like "carne" or "pane", which themselves end in "e"). This lookup is
# deliberately explicit and only ever merges a known pair — false negatives
# (a plural we don't recognise) just fall through unchanged, never a false
# merge of two different foods.
_IT_PLURAL_TO_SINGULAR: dict[str, str] = {
    "mele": "mela", "banane": "banana", "arance": "arancia", "pere": "pera",
    "pesche": "pesca", "albicocche": "albicocca", "prugne": "prugna",
    "ciliegie": "ciliegia", "fragole": "fragola", "angurie": "anguria",
    "meloni": "melone",
    "pomodori": "pomodoro", "patate": "patata", "cipolle": "cipolla",
    "carote": "carota", "zucchine": "zucchina", "melanzane": "melanzana",
    "peperoni": "peperone", "funghi": "fungo", "olive": "oliva",
    "broccoli": "broccolo", "carciofi": "carciofo", "zucche": "zucca",
    "insalate": "insalata", "lenticchie": "lenticchia", "ceci": "cece",
    "fagioli": "fagiolo", "piselli": "pisello",
    "uova": "uovo", "formaggi": "formaggio", "salumi": "salume",
    "prosciutti": "prosciutto", "salsicce": "salsiccia",
    "polpette": "polpetta", "bistecche": "bistecca", "cotolette": "cotoletta",
    "gamberi": "gambero", "gamberetti": "gamberetto", "cozze": "cozza",
    "vongole": "vongola", "acciughe": "acciuga", "sarde": "sarda",
    "mandorle": "mandorla", "noci": "noce", "nocciole": "nocciola",
    "pistacchi": "pistacchio", "arachidi": "arachide",
    "biscotti": "biscotto", "patatine": "patatina", "crostini": "crostino",
    "grissini": "grissino", "panini": "panino", "focacce": "focaccia",
    "torte": "torta", "cornetti": "cornetto", "ravioli": "raviolo",
    "tortellini": "tortellino", "gnocchi": "gnocco", "lasagne": "lasagna",
    "bruschette": "bruschetta", "fette": "fetta", "salse": "salsa",
}


def _singularize(token: str) -> str:
    """Merge a token's plural with its singular so cache keys and density-table
    lookups match regardless of wording. Checks the curated Italian map first,
    then falls back to a trivial English suffix rule ('eggs' -> 'egg').

    Intentionally conservative: only strips a trailing plural 's'/'es' on longer
    tokens, never touches numbers or short words.
    """
    if token in _IT_PLURAL_TO_SINGULAR:
        return _IT_PLURAL_TO_SINGULAR[token]
    if len(token) <= 3 or _NUM_RE.search(token):
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ses") or token.endswith("xes") or token.endswith("zes"):
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def canonicalize_food_name(text: str | None) -> str:
    """Return a stable canonical key for a food description.

    Steps: NFKC unicode fold -> lowercase -> strip punctuation -> collapse
    whitespace -> drop filler tokens -> trivial singularize -> sort tokens.
    Token-sorting makes 'banana and oatmeal' == 'oatmeal with banana'.

    Returns an empty string for empty/whitespace input.
    """
    if not text:
        return ""

    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _PUNCT_RE.sub(" ", folded)
    folded = _WS_RE.sub(" ", folded).strip()
    if not folded:
        return ""

    tokens = [
        _singularize(tok)
        for tok in folded.split(" ")
        if tok and tok not in _FILLER_TOKENS
    ]
    # If filtering removed everything (e.g. input was all filler), fall back to
    # the non-filler-stripped tokens so we never produce an empty key for real text.
    if not tokens:
        tokens = [_singularize(tok) for tok in folded.split(" ") if tok]

    tokens.sort()
    return " ".join(tokens)
