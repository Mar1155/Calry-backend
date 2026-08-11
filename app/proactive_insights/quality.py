import json
import re
from collections.abc import Iterable

from app.proactive_insights.candidates import InsightCandidate


class QualityGateError(ValueError):
    pass


class InsightQualityGate:
    _number = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?")
    _causality = re.compile(
        r"\b(because|caused?|causing|led to|leads to|resulted? in|due to|therefore|hence)\b",
        re.IGNORECASE,
    )
    _prohibited = re.compile(
        r"\b(cheat meal|clean eating|dirty food|guilty|sinful|bad food|good food|lazy|failure|"
        r"should be ashamed|you should|you must|avoid eating|need to|obese|diagnos(?:e|is)|"
        r"treat(?:ment)?|cure|medical advice|devi|dovresti|evita di mangiare|colpevole|"
        r"cibo cattivo|cibo buono)\b",
        re.IGNORECASE,
    )
    _positive = re.compile(r"\b(improv(?:e|ed|ing)|better|progress|more accurate|stronger)\b", re.IGNORECASE)
    _negative = re.compile(
        r"\b(wors(?:e|ened|ening)|regress(?:ed|ion)?|declin(?:e|ed|ing)|less accurate)\b", re.IGNORECASE
    )
    _judgment = re.compile(r"\b(healthy|unhealthy|good|bad|right|wrong)\b", re.IGNORECASE)

    def __init__(self, *, max_title_chars: int = 80, max_body_chars: int = 360, similarity_limit: float = 0.72):
        self.max_title_chars = max_title_chars
        self.max_body_chars = max_body_chars
        self.similarity_limit = similarity_limit

    @classmethod
    def _numbers(cls, value: object) -> set[str]:
        raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
        return {token.replace(",", ".").lstrip("+") for token in cls._number.findall(raw)}

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", value.casefold()))

    @classmethod
    def similarity(cls, left: str, right: str) -> float:
        left_tokens = cls._tokens(left)
        right_tokens = cls._tokens(right)
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def validate(
        self,
        candidate: InsightCandidate,
        *,
        title: str,
        body: str,
        evidence_refs: Iterable[str],
        recent_copy: Iterable[str] = (),
    ) -> None:
        title = title.strip()
        body = body.strip()
        if not title or not body or len(title) > self.max_title_chars or len(body) > self.max_body_chars:
            raise QualityGateError("copy_length")
        if len([part for part in re.split(r"[.!?。！？]+", body) if part.strip()]) > 2:
            raise QualityGateError("too_many_sentences")

        generated_numbers = self._numbers(f"{title} {body}")
        verified_numbers = self._numbers({"evidence": candidate.evidence, "metrics": candidate.metrics})
        if not generated_numbers.issubset(verified_numbers):
            raise QualityGateError("unverified_number")

        refs = list(evidence_refs)
        if not refs or any(self._resolve_ref(candidate, item) is None for item in refs):
            raise QualityGateError("unsupported_claim")

        copy = f"{title} {body}"
        if self._causality.search(copy):
            raise QualityGateError("unsupported_causality")
        if self._prohibited.search(copy):
            raise QualityGateError("prohibited_language")
        if candidate.direction == "negative" and self._positive.search(copy):
            raise QualityGateError("reversed_direction")
        if candidate.direction == "positive" and self._negative.search(copy):
            raise QualityGateError("reversed_direction")
        if candidate.direction == "neutral" and self._judgment.search(copy):
            raise QualityGateError("unsupported_judgment")

        for previous in recent_copy:
            if self.similarity(copy, previous) >= self.similarity_limit:
                raise QualityGateError("copy_too_similar")

    @staticmethod
    def _resolve_ref(candidate: InsightCandidate, reference: str) -> object | None:
        if not reference.startswith(("evidence.", "metrics.")):
            return None
        root_name, *parts = reference.split(".")
        current: object = candidate.evidence if root_name == "evidence" else candidate.metrics
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current
