from app.insights.patterns import VerifiedPattern


class PatternRanker:
    """Ranks verified facts and removes overlapping concepts deterministically."""

    @staticmethod
    def score(pattern: VerifiedPattern) -> float:
        return round(
            pattern.confidence
            * pattern.effect_size
            * pattern.novelty
            * pattern.user_relevance
            * pattern.actionability
            * pattern.freshness_weight,
            8,
        )

    def rank(self, patterns: list[VerifiedPattern], *, limit: int) -> list[VerifiedPattern]:
        ordered = sorted(
            patterns,
            key=lambda pattern: (
                -self.score(pattern),
                -pattern.confidence,
                -pattern.priority,
                -pattern.novelty,
                pattern.id,
            ),
        )
        selected: list[VerifiedPattern] = []
        seen_concepts: set[str] = set()
        for pattern in ordered:
            concept = pattern.concept or pattern.id
            if concept in seen_concepts:
                continue
            selected.append(pattern)
            seen_concepts.add(concept)
            if len(selected) >= limit:
                break
        return selected
