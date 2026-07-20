from collections.abc import Iterable

from app.insights import detectors as _detectors  # noqa: F401 - imports register detectors
from app.insights.detectors import PatternDetector
from app.insights.features import FeatureSnapshot
from app.insights.patterns import VerifiedPattern
from app.insights.ranking import PatternRanker


class InsightEngine:
    """Runs independent detectors, then ranks only their verified outputs."""

    def __init__(
        self,
        detectors: Iterable[PatternDetector] | None = None,
        ranker: PatternRanker | None = None,
    ) -> None:
        self.detectors = list(detectors) if detectors is not None else [cls() for cls in PatternDetector.registry]
        self.ranker = ranker or PatternRanker()

    def detect(self, snapshot: FeatureSnapshot) -> list[VerifiedPattern]:
        patterns: list[VerifiedPattern] = []
        for detector in self.detectors:
            patterns.extend(detector.detect(snapshot))
        return patterns

    def generate(self, snapshot: FeatureSnapshot, *, limit: int = 4) -> list[VerifiedPattern]:
        return self.ranker.rank(self.detect(snapshot), limit=limit)
