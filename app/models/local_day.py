"""Keeps a record's ``local_date`` (the user's calendar day) in step with ``created_at``."""

import datetime as dt

from sqlalchemy import event, inspect

from app.core.client_time import client_today, local_date


def _fill_on_insert(_mapper, _connection, target) -> None:
    if target.local_date is not None:
        return
    created_at = target.created_at
    target.local_date = local_date(created_at) if created_at is not None else client_today()


def _follow_created_at(_mapper, _connection, target) -> None:
    # Moving a record in time without naming its day (fixtures, admin tools)
    # re-derives the day in the active zone; an explicit local_date wins.
    state = inspect(target)
    if state.attrs.local_date.history.has_changes():
        return
    if state.attrs.created_at.history.has_changes() and isinstance(target.created_at, dt.datetime):
        target.local_date = local_date(target.created_at)


def track_local_date(model) -> None:
    event.listen(model, "before_insert", _fill_on_insert)
    event.listen(model, "before_update", _follow_created_at)
