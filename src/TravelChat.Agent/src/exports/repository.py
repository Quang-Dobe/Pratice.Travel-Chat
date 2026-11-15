from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from psycopg.rows import class_row

if TYPE_CHECKING:
    from src.main import DictRowPool

INSERT_SQL = """
INSERT INTO exports (id, conversation_id, card_id, format, file_name, content, draft)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

GET_SQL = """
SELECT format, file_name, content, draft FROM exports
WHERE id = %s AND conversation_id = %s
"""


@dataclass(frozen=True)
class ExportRow:
    format: str
    file_name: str
    content: bytes
    draft: bool


async def insert(
    pool: DictRowPool,
    conversation_id: UUID,
    card_id: str,
    format_: str,
    file_name: str,
    content: bytes,
    draft: bool,
) -> UUID:
    export_id = uuid4()
    async with pool.connection() as connection:
        await connection.execute(
            INSERT_SQL, (export_id, conversation_id, card_id, format_, file_name, content, draft)
        )
    return export_id


async def get(pool: DictRowPool, conversation_id: UUID, export_id: UUID) -> ExportRow | None:
    async with (
        pool.connection() as connection,
        connection.cursor(row_factory=class_row(ExportRow)) as cursor,
    ):
        await cursor.execute(GET_SQL, (export_id, conversation_id))
        return await cursor.fetchone()
