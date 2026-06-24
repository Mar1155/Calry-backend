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
}

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"\d")


def _singularize(token: str) -> str:
    """Trivial English singularization — enough to merge 'eggs'/'egg'.

    Intentionally conservative: only strips a trailing plural 's'/'es' on longer
    tokens, never touches numbers or short words.
    """
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
