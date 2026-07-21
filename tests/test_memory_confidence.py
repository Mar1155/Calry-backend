import datetime as dt

from app.memory.confidence import (
    DOMAIN_PARAMS,
    SOURCE_WEIGHTS,
    EvidenceItem,
    confidence_from_evidence,
    passes_gate,
    summarize_evidence,
)

NOW = dt.datetime(2026, 7, 21, 12, 0, tzinfo=dt.UTC)


def _items(n: int, *, span_days: int = 30, evidence_type: str = "confirmation") -> list[EvidenceItem]:
    out: list[EvidenceItem] = []
    for i in range(n):
        offset = int(span_days * i / max(1, n - 1))
        observed = NOW - dt.timedelta(days=offset)
        out.append(EvidenceItem(evidence_type, observed, observed.date()))
    return out


def test_confidence_is_reproducible() -> None:
    items = _items(6, span_days=40)
    first = confidence_from_evidence(items, "portion_model", consistency=0.9, now=NOW)
    second = confidence_from_evidence(items, "portion_model", consistency=0.9, now=NOW)
    assert first == second


def test_confidence_grows_with_evidence_then_saturates() -> None:
    small = confidence_from_evidence(_items(2), "portion_model", consistency=0.9, now=NOW)
    medium = confidence_from_evidence(_items(6), "portion_model", consistency=0.9, now=NOW)
    large = confidence_from_evidence(_items(50), "portion_model", consistency=0.9, now=NOW)
    assert small < medium <= large
    assert large <= 0.99


def test_confidence_decays_as_now_advances() -> None:
    items = _items(6, span_days=30)
    fresh = confidence_from_evidence(items, "portion_model", consistency=0.9, now=NOW)
    stale = confidence_from_evidence(items, "portion_model", consistency=0.9, now=NOW + dt.timedelta(days=400))
    assert stale < fresh


def test_corrections_weigh_at_least_as_much_as_confirmations() -> None:
    assert SOURCE_WEIGHTS["correction"] >= SOURCE_WEIGHTS["confirmation"]
    corrections = confidence_from_evidence(_items(6, evidence_type="correction"), "portion_model", consistency=0.9, now=NOW)
    confirmations = confidence_from_evidence(_items(6, evidence_type="confirmation"), "portion_model", consistency=0.9, now=NOW)
    assert corrections >= confirmations


def test_empty_evidence_yields_zero_and_fails_gate() -> None:
    assert confidence_from_evidence([], "portion_model", consistency=1.0, now=NOW) == 0.0
    assert passes_gate([], "portion_model", confidence=0.99) is False


def test_gate_enforces_minimums() -> None:
    params = DOMAIN_PARAMS["portion_model"]
    too_few = _items(params.min_evidence - 1, span_days=60)
    enough = _items(params.min_evidence, span_days=60)
    conf_few = confidence_from_evidence(too_few, "portion_model", consistency=0.9, now=NOW)
    conf_enough = confidence_from_evidence(enough, "portion_model", consistency=0.9, now=NOW)
    assert passes_gate(too_few, "portion_model", confidence=conf_few) is False
    assert passes_gate(enough, "portion_model", confidence=conf_enough) is True


def test_summarize_evidence_counts_distinct_days() -> None:
    items = _items(5, span_days=20)
    stats = summarize_evidence(items)
    assert stats.count == 5
    assert stats.span_days == 20
    assert stats.distinct_days == 5
    assert stats.last_observed_at == NOW
