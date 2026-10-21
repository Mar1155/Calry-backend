"""Diary days follow the user's timezone instead of UTC.

Adds ``users.timezone`` (IANA zone reported by the app in ``X-Timezone``) and a
``local_date`` on meals and burned calories: the calendar day the user lived
when logging. Existing rows are backfilled in the user's zone, and daily
summaries are rebuilt from the re-bucketed rows.

Users who never reported a zone are backfilled in ``LEGACY_TIMEZONE``: the app
only sent UTC-bucketed days until now and its audience is Italian, so this is
the closest guess for historical rows. Their next request stores the real zone
for everything logged afterwards.

Revision ID: client_timezone_days_0001
Revises: barcode_products_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "client_timezone_days_0001"
down_revision: str | None = "barcode_products_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_TIMEZONE = "Europe/Rome"


def _add_local_date(table: str, index: str) -> None:
    op.add_column(table, sa.Column("local_date", sa.Date(), nullable=True))
    # meals carries deferred constraint triggers (ingredient invariants). The
    # backfill only sets local_date, so it must not queue their events:
    # Postgres refuses the ALTER TABLE below while any are pending
    # ("cannot ALTER TABLE ... because it has pending trigger events").
    op.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
    op.execute(
        sa.text(
            f"""
            UPDATE {table} AS t
            SET local_date = (t.created_at AT TIME ZONE COALESCE(u.timezone, :legacy))::date
            FROM users AS u
            WHERE u.id = t.user_id
            """
        ).bindparams(legacy=LEGACY_TIMEZONE)
    )
    op.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
    op.alter_column(table, "local_date", nullable=False)
    op.create_index(index, table, ["user_id", "local_date"])


def upgrade() -> None:
    op.add_column("users", sa.Column("timezone", sa.String(64), nullable=True))
    # The notification settings already hold a device zone for some users.
    op.execute(
        """
        UPDATE users AS u
        SET timezone = p.timezone
        FROM insight_notification_preferences AS p
        WHERE p.user_id = u.id AND p.timezone IS NOT NULL AND p.timezone <> 'UTC'
        """
    )

    _add_local_date("meals", "ix_meals_user_local_date")
    _add_local_date("burned_calories", "ix_burned_calories_user_local_date")

    # Rebuild every summary from the re-bucketed rows (same arithmetic as
    # SummaryService.sync_daily_summary). Water counts stay on their rows.
    op.execute(
        """
        INSERT INTO daily_summaries (user_id, date, consumed_calories, burned_calories, remaining_calories, water_glasses)
        SELECT user_id, local_date, 0, 0, 0, 0 FROM meals
        UNION
        SELECT user_id, local_date, 0, 0, 0, 0 FROM burned_calories
        ON CONFLICT ON CONSTRAINT uq_user_date DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE daily_summaries AS s
        SET consumed_calories = totals.consumed,
            burned_calories = totals.burned,
            remaining_calories = u.daily_calorie_goal - totals.consumed + totals.burned
        FROM (
            SELECT d.id,
                   COALESCE((SELECT SUM(m.estimated_calories) FROM meals AS m
                             WHERE m.user_id = d.user_id AND m.local_date = d.date), 0) AS consumed,
                   COALESCE((SELECT SUM(b.calories) FROM burned_calories AS b
                             WHERE b.user_id = d.user_id AND b.local_date = d.date), 0) AS burned
            FROM daily_summaries AS d
        ) AS totals, users AS u
        WHERE totals.id = s.id AND u.id = s.user_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_burned_calories_user_local_date", table_name="burned_calories")
    op.drop_column("burned_calories", "local_date")
    op.drop_index("ix_meals_user_local_date", table_name="meals")
    op.drop_column("meals", "local_date")
    op.drop_column("users", "timezone")
