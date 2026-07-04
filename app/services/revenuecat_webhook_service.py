"""Orchestrates RevenueCat webhook processing.

Flow per event:
1. Persist the event in the ``revenuecat_events`` ledger (idempotency + audit).
2. Resolve the backend user from the event's app_user_id / aliases.
3. Verify the *current* entitlement state against the RevenueCat REST API
   (fallback: derive conservatively from the payload when the API is
   unavailable or not configured).
4. Update ``user.is_premium`` and related premium metadata.

Design decisions (documented assumptions):
- ``is_premium`` always mirrors the entitlement's expiration, never the event
  type. A CANCELLATION therefore keeps premium until the paid period ends;
  the later EXPIRATION event (or any subsequent webhook) flips it off.
- Sandbox events are processed like production events: App Review and
  TestFlight purchases go through the Apple sandbox and must unlock premium.
  The environment is logged and stored for filtering.
- TRANSFER events re-evaluate every user on both sides via the REST API. If
  the API key is not configured they are parked as ``needs_reconciliation``
  because the payload alone does not say who ends up entitled.
- Unknown users return 200 (``ignored``): retrying would not help, and the
  ledger row (``user_not_found``) allows later reconciliation.
- Processing failures return 500 so RevenueCat redelivers; the ledger row is
  marked ``failed`` and redelivery reprocesses it (only ``processed`` events
  are skipped as duplicates).
"""

import datetime as dt
import logging
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.revenuecat_event import RevenueCatEvent
from app.models.user import User
from app.repositories.revenuecat_event import RevenueCatEventRepository
from app.repositories.user import UserRepository
from app.schemas.revenuecat import RevenueCatEventPayload
from app.services.revenuecat_service import (
    EntitlementState,
    RevenueCatAPIError,
    RevenueCatClient,
    derive_entitlement_state,
)

logger = logging.getLogger("app.services.revenuecat_webhook")

# Event types RevenueCat may deliver; anything else is processed identically
# (entitlement re-check) but logged as unrecognized for visibility.
KNOWN_EVENT_TYPES = {
    "TEST",
    "INITIAL_PURCHASE",
    "RENEWAL",
    "EXPIRATION",
    "CANCELLATION",
    "UNCANCELLATION",
    "BILLING_ISSUE",
    "PRODUCT_CHANGE",
    "REFUND",
    "NON_RENEWING_PURCHASE",
    "SUBSCRIPTION_PAUSED",
    "SUBSCRIPTION_EXTENDED",
    "TRANSFER",
}


class RevenueCatWebhookService:
    def __init__(self, db: AsyncSession, client: RevenueCatClient | None = None):
        self.db = db
        self.client = client or RevenueCatClient()
        self.event_repo = RevenueCatEventRepository(db)
        self.user_repo = UserRepository(db)

    async def process(self, payload: dict[str, Any]) -> dict[str, str]:
        """Processes one webhook delivery. Returns the JSON response body."""
        event_data = payload.get("event")
        if not event_data or not isinstance(event_data, dict):
            logger.warning("revenuecat_webhook_rejected reason=missing_event_object")
            return {"status": "ignored", "reason": "No event object found"}

        try:
            event = RevenueCatEventPayload.model_validate(event_data)
        except ValidationError as exc:
            logger.warning("revenuecat_webhook_rejected reason=malformed_event errors=%s", exc.error_count())
            return {"status": "ignored", "reason": "Malformed event payload"}

        event_id = event.idempotency_key(event_data)
        self._log_received(event, event_id)

        # --- Idempotency ledger -------------------------------------------------
        record = await self.event_repo.get_by_event_id(event_id)
        if record and record.processing_status == "processed":
            logger.info("revenuecat_webhook_duplicate event_id=%s type=%s", event_id, event.type)
            return {"status": "duplicate", "event_processed": event.type}
        if record is None:
            record = RevenueCatEvent(
                event_id=event_id,
                event_type=event.type,
                app_user_id=event.app_user_id,
                product_id=event.product_id,
                entitlement_ids=event.all_entitlement_ids() or None,
                transaction_id=event.transaction_id,
                original_transaction_id=event.original_transaction_id,
                expiration_at_ms=event.expiration_at_ms,
                environment=event.environment,
                store=event.store,
                payload=payload,
            )
            self.db.add(record)
            try:
                # Commit the ledger row on its own so the audit trail survives
                # any downstream processing failure.
                await self.db.commit()
            except IntegrityError:
                # A concurrent delivery of the same event won the insert race.
                await self.db.rollback()
                logger.info("revenuecat_webhook_duplicate event_id=%s type=%s race=true", event_id, event.type)
                return {"status": "duplicate", "event_processed": event.type}

        try:
            return await self._process_event(event, event_id, record)
        except HTTPException:
            raise
        except Exception as exc:
            await self.db.rollback()
            await self._mark_event(event_id, "failed", f"{type(exc).__name__}: {exc}")
            logger.exception("revenuecat_webhook_failed event_id=%s type=%s", event_id, event.type)
            # Non-2xx makes RevenueCat redeliver; the ledger guarantees the
            # retry is safe to reprocess.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Webhook processing failed; RevenueCat will retry.",
            ) from exc

    # --- Core processing --------------------------------------------------------

    async def _process_event(
        self,
        event: RevenueCatEventPayload,
        event_id: str,
        record: RevenueCatEvent,
    ) -> dict[str, str]:
        if event.type not in KNOWN_EVENT_TYPES:
            logger.warning("revenuecat_webhook_unknown_type event_id=%s type=%s", event_id, event.type)

        if event.type == "TEST":
            await self._mark_event(event_id, "processed")
            await self.db.commit()
            return {"status": "success", "event_processed": event.type}

        if event.type == "TRANSFER":
            return await self._process_transfer(event, event_id)

        candidates = event.candidate_app_user_ids()
        if not candidates:
            logger.warning("revenuecat_webhook_ignored event_id=%s type=%s reason=no_app_user_id", event_id, event.type)
            await self._mark_event(event_id, "ignored", "No app_user_id in event")
            await self.db.commit()
            return {"status": "ignored", "reason": "No app_user_id"}

        user, matched_app_user_id = await self._resolve_user(candidates)
        if user is None:
            logger.warning(
                "revenuecat_webhook_user_mismatch event_id=%s type=%s app_user_id=%s candidates=%d",
                event_id,
                event.type,
                event.app_user_id,
                len(candidates),
            )
            await self._mark_event(event_id, "user_not_found", f"No user for candidates: {candidates}")
            await self.db.commit()
            return {"status": "ignored", "reason": "User not found"}

        app_user_id = event.app_user_id or matched_app_user_id
        state, source = await self._resolve_entitlement_state(event, user, app_user_id)
        await self._apply_state(user, state, app_user_id)
        await self._mark_event(event_id, "processed")
        await self.db.commit()

        logger.info(
            "revenuecat_premium_updated event_id=%s type=%s user_id=%s is_premium=%s "
            "expires_at=%s source=%s environment=%s store=%s",
            event_id,
            event.type,
            user.id,
            state.is_active,
            state.expires_at.isoformat() if state.expires_at else None,
            source,
            event.environment,
            state.store or event.store,
        )
        return {"status": "success", "event_processed": event.type}

    async def _process_transfer(self, event: RevenueCatEventPayload, event_id: str) -> dict[str, str]:
        """Re-evaluates both sides of a TRANSFER via the REST API."""
        if not self.client.is_configured:
            logger.warning(
                "revenuecat_webhook_transfer_parked event_id=%s reason=no_api_key", event_id
            )
            await self._mark_event(
                event_id, "needs_reconciliation", "TRANSFER requires REVENUECAT_API_KEY to resolve both sides"
            )
            await self.db.commit()
            return {"status": "accepted", "reason": "Transfer parked for reconciliation"}

        sides = [*(event.transferred_to or []), *(event.transferred_from or [])]
        updated = 0
        for app_user_id in dict.fromkeys(sides):
            user, _ = await self._resolve_user([app_user_id])
            if user is None:
                continue
            subscriber = await self.client.get_subscriber(app_user_id)
            state = derive_entitlement_state(subscriber, settings.REVENUECAT_ENTITLEMENT_ID)
            await self.event_repo.add_snapshot(
                app_user_id=app_user_id,
                user_id=user.id,
                entitlement_active=state.is_active,
                expires_at=state.expires_at,
                snapshot=subscriber,
            )
            await self._apply_state(user, state, app_user_id)
            updated += 1
            logger.info(
                "revenuecat_premium_updated event_id=%s type=TRANSFER user_id=%s is_premium=%s source=api",
                event_id,
                user.id,
                state.is_active,
            )

        await self._mark_event(event_id, "processed")
        await self.db.commit()
        return {"status": "success", "event_processed": event.type, "users_updated": str(updated)}

    # --- Helpers ----------------------------------------------------------------

    async def _resolve_user(self, candidates: list[str]) -> tuple[User | None, str | None]:
        """Returns the user and the candidate id that matched them."""
        for candidate in candidates:
            user = await self.user_repo.get_by_firebase_uid(candidate)
            if user:
                return user, candidate
        for candidate in candidates:
            user = await self.user_repo.get_by_revenuecat_app_user_id(candidate)
            if user:
                return user, candidate
        return None, None

    async def _resolve_entitlement_state(
        self, event: RevenueCatEventPayload, user: User, app_user_id: str
    ) -> tuple[EntitlementState, str]:
        """Returns the entitlement state and its source ("api" or "payload").

        The REST API is the source of truth. The payload fallback only runs
        when the API is not configured or temporarily unavailable, and it is
        conservative: entitlement present + expiration in the future.
        """
        if self.client.is_configured:
            try:
                subscriber = await self.client.get_subscriber(app_user_id)
            except RevenueCatAPIError as exc:
                logger.error(
                    "revenuecat_api_fallback app_user_id=%s error=%s — deriving state from webhook payload",
                    app_user_id,
                    exc,
                )
            else:
                state = derive_entitlement_state(subscriber, settings.REVENUECAT_ENTITLEMENT_ID)
                await self.event_repo.add_snapshot(
                    app_user_id=app_user_id,
                    user_id=user.id,
                    entitlement_active=state.is_active,
                    expires_at=state.expires_at,
                    snapshot=subscriber,
                )
                return state, "api"

        return self._derive_state_from_payload(event), "payload"

    @staticmethod
    def _derive_state_from_payload(event: RevenueCatEventPayload) -> EntitlementState:
        """Conservative payload-only derivation used when the API is unreachable.

        Mirrors the expiration-based semantics of the API path: the premium
        entitlement must be present and not expired. EXPIRATION/REFUND clear
        it; CANCELLATION alone does not (the period is already paid).
        """
        now = dt.datetime.now(dt.UTC)
        expires_at = (
            dt.datetime.fromtimestamp(event.expiration_at_ms / 1000.0, tz=dt.UTC)
            if event.expiration_at_ms is not None
            else None
        )
        has_entitlement = settings.REVENUECAT_ENTITLEMENT_ID in event.all_entitlement_ids()
        if event.type in ("EXPIRATION", "REFUND"):
            is_active = False
        else:
            is_active = has_entitlement and (expires_at is None or expires_at > now)
        return EntitlementState(
            is_active=is_active,
            expires_at=expires_at,
            product_id=event.product_id,
            store=event.store,
        )

    async def _apply_state(self, user: User, state: EntitlementState, app_user_id: str) -> None:
        await self.user_repo.update_user_premium_status(
            user=user,
            is_premium=state.is_active,
            premium_entitlement=settings.REVENUECAT_ENTITLEMENT_ID if state.is_active else None,
            premium_expires_at=state.expires_at,
            revenuecat_app_user_id=app_user_id,
            premium_store=state.store,
            premium_product_id=state.product_id,
        )

    async def _mark_event(self, event_id: str, event_status: str, error: str | None = None) -> None:
        """Marks the ledger row by id (safe after a session rollback)."""
        record = await self.event_repo.get_by_event_id(event_id)
        if record is None:  # pragma: no cover — ledger row committed earlier
            logger.error("revenuecat_webhook_ledger_missing event_id=%s", event_id)
            return
        await self.event_repo.mark_status(record, event_status, error)
        if event_status == "failed":
            await self.db.commit()

    @staticmethod
    def _log_received(event: RevenueCatEventPayload, event_id: str) -> None:
        logger.info(
            "revenuecat_webhook_received event_id=%s type=%s app_user_id=%s product_id=%s "
            "entitlement_ids=%s transaction_id=%s original_transaction_id=%s "
            "expiration_at_ms=%s environment=%s store=%s",
            event_id,
            event.type,
            event.app_user_id,
            event.product_id,
            event.all_entitlement_ids(),
            event.transaction_id,
            event.original_transaction_id,
            event.expiration_at_ms,
            event.environment,
            event.store,
        )
