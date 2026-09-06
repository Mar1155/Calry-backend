# Proactive Insight Engine and Insight Diary

Calry creates permanent, evidence-backed observations from user events and local-time evaluations. Events are opportunities to evaluate—not notifications and not guaranteed insights.

## Architecture

1. `InsightVersionService` increments the existing source versions and writes an idempotent `proactive_insight_events` inbox row in the same transaction as the user change.
2. Celery processes the event. A five-minute inbox sweep recovers missed broker enqueueing. A 15-minute timezone-aware sweep stages due daily, weekly, and monthly evaluations with stable IDs.
3. Existing `FeatureExtractor`, `PatternDetector.registry`, ranking, and source snapshots produce deterministic features and verified patterns. Candidate factories add deterministic milestone and repeated-meal candidates.
4. Confidence, significance, novelty, and usefulness thresholds run before any LLM call. Exact IDs, type cooldowns, and semantic fingerprints prevent repetitive candidates.
5. The configured small model receives only the verified candidate JSON. It returns strict JSON copy; it never computes nutrition metrics.
6. The quality gate rejects absent numbers, direction reversal, unsupported claims/causality, medical or judgmental language, overly long copy, and copy too similar to recent entries.
7. Accepted copy becomes a permanent `proactive_insights` diary row. A stronger continuation can link to and supersede an earlier row without deleting it.
8. Notification scoring marks only strong candidates ready. Scheduling checks preferences and quiet hours; delivery rechecks all policy conditions immediately before FCM.

The three notification phases remain separate: insight creation, notification eligibility/scheduling, and external delivery. Push failure never removes a diary entry.

## Supported triggers

Source events:

- `MealCreated`, `MealUpdated`, `MealCorrected`, `MealDeleted`, `MealCategoryChanged`
- `ActivityLogged`, `ActivityDeleted`
- `WaterLogged`, `WaterRemoved`
- `TargetChanged`, `WeightUpdated`, `ProfileChanged`

Derived candidate triggers:

- daily calorie milestone: 25%, 50%, 75%, near target, over target
- meaningful macro change
- repeated food or meal
- logging behavior change
- AI estimation accuracy improvement/regression
- habit or trend detection
- previously uncertain evidence becoming sufficient

Scheduled triggers: `DailyEvaluation`, `WeeklyEvaluation`, and `MonthlyEvaluation`. The scheduler uses each active premium account’s preference-row IANA timezone. Daily evaluation begins after 18:00 local, weekly on Sunday after 18:00 local, and monthly on day one after 10:00 local. Repeated sweeps are harmless because event IDs use the local period. Event-driven inbox rows for a non-premium account complete without generating or notifying; this matches diary authorization.

## Persistence and migrations

Migration `2026_08_11_0001_proactive_insight_engine.py` (`e2f3a4b5c6d7`) adds:

- `proactive_insight_events`: transactional inbox, attempts, result, source versions
- `proactive_insights`: permanent diary copy/evidence, scores, stable IDs, read state, related insight, model/prompt version, notification state

Migration `2026_08_11_0002_insight_notifications.py` (`f3a4b5c6d7e8`) adds:

- `proactive_insights.superseded_at`
- `insight_notification_preferences`: proactive/daily/weekly switches, quiet hours, IANA timezone
- `insight_notification_deliveries`: one delivery ledger per insight, stable idempotency key, scheduling, attempts, FCM metadata, errors and suppression reason
- `insight_analytics_events`: privacy-minimal product events; generated meal text is never copied here

User deletion cascades these records. Related links use `SET NULL`; old observations otherwise remain in the diary.

## API

- `GET /api/v1/insights/diary`
- `GET /api/v1/insights/diary/unread`
- `GET /api/v1/insights/diary/{id}`
- `PATCH /api/v1/insights/diary/{id}/read`
- `GET /api/v1/insights/preferences`
- `PATCH /api/v1/insights/preferences`
- `POST /api/v1/insights/analytics`
- `POST /api/v1/users/me/fcm-token`
- `DELETE /api/v1/users/me/fcm-token`

Diary endpoints retain existing premium-insight authorization. Push preferences and device registration are account-owned and do not control diary creation.

## Push delivery flow

1. A high-scoring persisted insight gets a delivery ledger with `scheduled_for`.
2. Quiet hours move `scheduled_for` to their local end; the copy is not regenerated.
3. The minute sweep queues due delivery IDs.
4. The worker locks the ledger and rechecks global enablement, user switches, daily/weekly type switches, token, local quiet hours, local-day cap, semantic cooldown, age, supersession, and prior send.
5. The worker commits an at-most-once `sending` claim before calling Firebase Admin. FCM carries a visible notification and trusted `insight_id`; APNs delivery is routed through FCM.
6. Success stores FCM message ID, timestamps the ledger and insight, and emits `notification_sent`. Known transient failures retry with exponential delay. Permanent token failures clear the stored token. All failures preserve the diary.

Routine notifications use normal Android priority and APNs priority 5. Copy is deliberately quiet: a neutral Calry title plus the already quality-gated insight title. No numeric badge is requested by the app.

## Suppression rules

- engine or push rollout disabled
- notification score below threshold
- proactive notifications disabled
- daily/weekly evaluation notification disabled
- missing or invalid device token
- active local quiet hours (delayed, not discarded)
- one ordinary proactive push already sent in the user’s local day
- same semantic/type cooldown still active
- insight older than configured maximum age
- insight superseded
- insight already sent
- source row missing
- permanent provider failure or exhausted transient retries

Configuration is environment-backed: minimum candidate scores, notification score threshold, default and per-type cooldowns, daily cap, maximum notification age, retry count/base delay, model, and rollout switches.

## Frontend architecture

Flutter feature code lives under `lib/features/insights/`:

- domain models parse diary entries and present a small, human-readable evidence whitelist
- repository owns diary, read, preference, token, and privacy-minimal analytics API calls
- Riverpod diary state distinguishes loading, empty, ready, refreshing, stale, and unavailable while retaining the last valid entries
- diary UI groups warm journal cards by date; unread uses both a soft dot and the word “New”/screen-reader label
- detail UI waits 800 ms of actual display before recording `insight_viewed` and marking read
- settings request notification permission only after an explicit enable action; daily, weekly, quiet hours, and device timezone remain separate
- Firebase Messaging restores authorized tokens, handles rotation, refreshes foreground diary data, and deep-links trusted insight payloads

Home uses the living Calry “C” beside the calorie balance as its single Insight Diary entry point. It has no permanent label, badge, or counter. Unread observations give the mark a restrained warm glow and an occasional shimmer; a newly received observation travels into the C as a small particle before a gentle pulse. Tapping the C gives a soft spring-and-haptic response and opens the diary. Reduced Motion replaces spatial effects with opacity. The onboarding result explains this visual language once, while Home remains free of AI cards and notification-feed chrome.

## Analytics

Stored events include `insight_created`, `insight_viewed`, `insight_marked_read`, `diary_opened`, `notification_eligible`, `notification_scheduled`, `notification_sent`, `notification_failed`, `notification_opened`, `notification_suppressed`, `notification_preferences_changed`, and optional dismiss events. Category, source, timestamps, and suppression reason support aggregate open/read/conversion/opt-out/frequency/category metrics without logging meal copy.

## Production calibration and platform risks

Still tune from real opt-in cohorts: candidate thresholds, notification threshold, category cooldowns, max age, local evaluation hours, retry delay, and the one-push cap. No admin dashboard was added; environment configuration is the control surface.

Platform operations still required:

- Upload/verify the APNs authentication key in the Firebase project and enable Push Notifications for the production App ID/provisioning profile.
- Confirm the checked-in production `aps-environment` matches every signing flavor; development builds may need flavor-specific entitlements.
- Verify Android 13 permission behavior and OEM delivery on physical devices.
- Current backend storage has one FCM token per user, so a newer device replaces an older one. Multi-device delivery needs a device-token table.
- The at-most-once claim intentionally leaves a row in `sending` after an ambiguous worker crash to prevent duplicate interruption. Production reconciliation may inspect these rows, but must not resend blindly.
