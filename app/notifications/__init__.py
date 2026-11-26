"""Push notification delivery shared by every notification type.

Types (proactive insights today, reminders and recaps later) create rows in the
delivery ledger through :class:`PushDispatcher`. The dispatcher owns the rules
that must hold across types: per-category opt-outs, quiet hours in the user's
own timezone, the global daily and weekly caps, multi-device fan-out and
at-most-once sending. Type-specific rules live in a :class:`TypePolicy`.
"""
