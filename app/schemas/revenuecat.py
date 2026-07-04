import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class RevenueCatEventPayload(BaseModel):
    """Lenient view over the ``event`` object of a RevenueCat webhook.

    Unknown fields are preserved-by-ignore (the raw payload is stored verbatim
    in the events ledger), so RevenueCat can add fields without breaking us.
    """

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    type: str
    app_user_id: str | None = None
    original_app_user_id: str | None = None
    aliases: list[str] | None = None
    product_id: str | None = None
    entitlement_id: str | None = None
    entitlement_ids: list[str] | None = None
    transaction_id: str | None = None
    original_transaction_id: str | None = None
    expiration_at_ms: int | None = None
    event_timestamp_ms: int | None = None
    environment: str | None = None
    store: str | None = None
    # TRANSFER events carry these instead of app_user_id.
    transferred_from: list[str] | None = None
    transferred_to: list[str] | None = None

    def idempotency_key(self, raw_event: dict[str, Any]) -> str:
        """Returns the stable idempotency key: RevenueCat's event id, or a
        content hash when the payload has none."""
        if self.id:
            return self.id
        digest = hashlib.sha256(json.dumps(raw_event, sort_keys=True, default=str).encode()).hexdigest()
        return f"sha256:{digest}"

    def all_entitlement_ids(self) -> list[str]:
        """Merges the deprecated singular field with the list field."""
        ids = list(self.entitlement_ids or [])
        if self.entitlement_id and self.entitlement_id not in ids:
            ids.append(self.entitlement_id)
        return ids

    def candidate_app_user_ids(self) -> list[str]:
        """All identifiers that may map to a backend user, primary first.

        Anonymous RevenueCat ids ($RCAnonymousID:...) can never match a
        Firebase UID but are kept as a last resort for the
        ``revenuecat_app_user_id`` column lookup.
        """
        seen: list[str] = []
        for candidate in [self.app_user_id, self.original_app_user_id, *(self.aliases or [])]:
            if candidate and candidate not in seen:
                seen.append(candidate)
        # Non-anonymous ids first: they are the Firebase UIDs we key users on.
        return sorted(seen, key=lambda c: c.startswith("$RCAnonymousID:"))
