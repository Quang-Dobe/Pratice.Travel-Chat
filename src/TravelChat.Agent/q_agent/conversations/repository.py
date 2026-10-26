from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from psycopg.rows import class_row

if TYPE_CHECKING:
    from q_agent.main import DictRowPool

LIST_SQL = """
SELECT id, title, created_at FROM conversations
WHERE user_id = %s
ORDER BY created_at DESC
"""

CREATE_SQL = """
INSERT INTO conversations (id, user_id, title)
VALUES (%s, %s, %s)
RETURNING id, title, created_at
"""

GET_SQL = """
SELECT id, title, created_at FROM conversations
WHERE id = %s AND user_id = %s
"""


@dataclass(frozen=True)
class ConversationRow:
    id: UUID
    title: str
    created_at: datetime


async def list_for_user(pool: DictRowPool, uid: UUID) -> list[ConversationRow]:
    async with (
        pool.connection() as connection,
        connection.cursor(row_factory=class_row(ConversationRow)) as cursor,
    ):
        await cursor.execute(LIST_SQL, (uid,))
        return await cursor.fetchall()


async def create(pool: DictRowPool, uid: UUID, title: str) -> ConversationRow:
    async with (
        pool.connection() as connection,
        connection.cursor(row_factory=class_row(ConversationRow)) as cursor,
    ):
        await cursor.execute(CREATE_SQL, (uuid4(), uid, title))
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING produced no row")
    return row


async def get_for_user(pool: DictRowPool, id_: UUID, uid: UUID) -> ConversationRow | None:
    async with (
        pool.connection() as connection,
        connection.cursor(row_factory=class_row(ConversationRow)) as cursor,
    ):
        await cursor.execute(GET_SQL, (id_, uid))
        return await cursor.fetchone()
