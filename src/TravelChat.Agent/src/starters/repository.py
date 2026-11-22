from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from psycopg.rows import class_row

if TYPE_CHECKING:
    from src.main import DictRowPool

MAX_PREFERENCES = 3

UPSERT_SQL = """
INSERT INTO trip_summaries
    (conversation_id, user_id, destination, start_date, end_date, trip_type, preferences)
SELECT id, user_id, %s::text, %s::date, %s::date, %s::text, %s::text[]
FROM conversations
WHERE id = %s AND user_id = %s
ON CONFLICT (conversation_id) DO UPDATE SET
    destination = EXCLUDED.destination,
    start_date = EXCLUDED.start_date,
    end_date = EXCLUDED.end_date,
    trip_type = EXCLUDED.trip_type,
    preferences = EXCLUDED.preferences,
    updated_at = now()
WHERE trip_summaries.user_id = EXCLUDED.user_id
"""

NEWEST_SQL = """
SELECT conversation_id, destination, start_date, end_date, trip_type, preferences, updated_at
FROM trip_summaries
WHERE user_id = %s
ORDER BY updated_at DESC
LIMIT %s
"""

CLEAR_SQL = """
DELETE FROM trip_summaries
WHERE user_id = %s
"""

GET_ENABLED_SQL = """
SELECT enabled FROM suggestion_settings
WHERE user_id = %s
"""

SET_ENABLED_SQL = """
INSERT INTO suggestion_settings (user_id, enabled)
VALUES (%s, %s)
ON CONFLICT (user_id) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()
RETURNING enabled
"""


@dataclass(frozen=True)
class SummaryRow:
    conversation_id: UUID
    destination: str
    start_date: date | None
    end_date: date | None
    trip_type: str | None
    preferences: list[str]
    updated_at: datetime


def _to_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


async def upsert_summary(
    pool: DictRowPool,
    conversation_id: UUID,
    uid: UUID,
    *,
    destination: str,
    start_date: str | None,
    end_date: str | None,
    trip_type: str | None,
    preferences: list[str],
) -> bool:
    params = (
        destination,
        _to_date(start_date),
        _to_date(end_date),
        trip_type,
        list(preferences)[:MAX_PREFERENCES],
        conversation_id,
        uid,
    )
    async with pool.connection() as connection:
        cursor = await connection.execute(UPSERT_SQL, params)
        return cursor.rowcount > 0


async def newest_summaries(pool: DictRowPool, uid: UUID, limit: int = 5) -> list[SummaryRow]:
    async with (
        pool.connection() as connection,
        connection.cursor(row_factory=class_row(SummaryRow)) as cursor,
    ):
        await cursor.execute(NEWEST_SQL, (uid, limit))
        return await cursor.fetchall()


async def clear_summaries(pool: DictRowPool, uid: UUID) -> int:
    async with pool.connection() as connection:
        cursor = await connection.execute(CLEAR_SQL, (uid,))
        return cursor.rowcount


async def get_enabled(pool: DictRowPool, uid: UUID) -> bool:
    async with pool.connection() as connection:
        cursor = await connection.execute(GET_ENABLED_SQL, (uid,))
        row = await cursor.fetchone()
    return True if row is None else bool(row["enabled"])


async def set_enabled(pool: DictRowPool, uid: UUID, enabled: bool) -> bool:
    async with pool.connection() as connection:
        cursor = await connection.execute(SET_ENABLED_SQL, (uid, enabled))
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING produced no row")
    return bool(row["enabled"])
