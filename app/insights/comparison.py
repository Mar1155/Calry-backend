import json
from enum import StrEnum
from hashlib import sha256
from typing import Any

from app.insights.patterns import VerifiedPattern


class PatternChange(StrEnum):
    UNCHANGED = "unchanged"
    MINIMAL = "minimally_changed"
    MATERIAL = "materially_changed"
    REPLACED = "replaced"
    NO_LONGER_VALID = "no_longer_valid"
    NEW = "new"


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


class PatternComparer:
    """Detector-specific materiality rules. Direction comes only from payloads."""

    @staticmethod
    def _changed(previous: dict[str, Any], current: dict[str, Any], key: str, threshold: float) -> bool:
        old = previous.get(key)
        new = current.get(key)
        return isinstance(old, (int, float)) and isinstance(new, (int, float)) and abs(new - old) >= threshold

    def compare(
        self,
        previous: dict[str, Any] | None,
        current: VerifiedPattern | None,
    ) -> PatternChange:
        if previous is None and current is None:
            return PatternChange.UNCHANGED
        if current is None:
            return PatternChange.NO_LONGER_VALID
        if previous is None:
            return PatternChange.NEW
        if previous.get("pattern_key") != current.id:
            return PatternChange.REPLACED

        old = previous.get("payload_json", {})
        new = current.payload
        if payload_hash(old) == payload_hash(new):
            return PatternChange.UNCHANGED

        pattern_id = current.id
        material = False
        if pattern_id == "goal_consistency":
            old_rate = float(old.get("adherence_rate", 0))
            new_rate = float(new.get("adherence_rate", 0))
            old_band = sum(old_rate >= boundary for boundary in (0.25, 0.5, 0.75, 0.9))
            new_band = sum(new_rate >= boundary for boundary in (0.25, 0.5, 0.75, 0.9))
            material = old.get("days_within_target") != new.get("days_within_target") or old_band != new_band
        elif pattern_id == "macro_balance":
            target_ratios = ("protein_target_ratio", "carbs_target_ratio", "fat_target_ratio")
            material = any(
                self._changed(old, new, key, 0.025)
                for key in ("protein_calorie_share", "carbs_calorie_share", "fat_calorie_share")
            ) or any(self._changed(old, new, key, 0.10) for key in target_ratios)
            material = (
                material
                or old.get("largest_target_gap") != new.get("largest_target_gap")
                or any(
                    isinstance(old.get(key), (int, float))
                    and isinstance(new.get(key), (int, float))
                    and (old[key] - 1) * (new[key] - 1) <= 0
                    and old[key] != new[key]
                    for key in target_ratios
                )
            )
        elif pattern_id in {"ai_estimation_accuracy", "ai_accuracy_trend"}:
            material = (
                self._changed(old, new, "accuracy_rate_within_ten_percent", 0.04)
                or self._changed(old, new, "accepted_without_changes_rate", 0.04)
                or self._changed(old, new, "median_absolute_correction_percent", 2.0)
                or self._changed(old, new, "absolute_percentage_point_change", 2.0)
                or old.get("direction") != new.get("direction")
                or old.get("accepted_without_changes_rate_band") != new.get("accepted_without_changes_rate_band")
            )
        elif pattern_id == "meal_distribution":
            material = old.get("most_logged_category") != new.get("most_logged_category") or self._changed(
                old, new, "dominant_category_share", 0.05
            )
        elif pattern_id == "activity_frequency":
            material = self._changed(old, new, "active_day_rate", 0.15) or self._changed(
                old, new, "active_day_rate_change", 0.15
            )
        elif pattern_id == "hydration_consistency":
            material = self._changed(old, new, "average_glasses", 1.0) or self._changed(
                old, new, "average_glasses_change", 1.0
            )
        elif pattern_id == "logging_consistency":
            material = old.get("longest_streak") != new.get("longest_streak") or self._changed(
                old, new, "logging_rate", 0.10
            )
        elif pattern_id in {"calories_trend", "goal_adherence_change", "weekend_difference"}:
            material = (
                old.get("direction") != new.get("direction")
                or self._changed(old, new, "change_percent", 0.05)
                or self._changed(old, new, "adherence_rate_change", 0.10)
                or self._changed(old, new, "difference_percent", 0.08)
            )
        else:
            material = old != new
        return PatternChange.MATERIAL if material else PatternChange.MINIMAL
