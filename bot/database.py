"""
bot/database.py
---------------
Async SQLite helpers powered by aiosqlite.

Usage
-----
    from bot.database import init_db, get_db, db_fetchone, db_fetchall, db_execute, db_scalar

    # On startup (call once):
    await init_db()

    # Ad-hoc queries:
    row  = await db_fetchone("SELECT * FROM config WHERE key = ?", "mode")
    rows = await db_fetchall("SELECT * FROM signals ORDER BY created_at DESC LIMIT 10")
    await db_execute("UPDATE config SET value = ? WHERE key = ?", "intraday", "mode")
    val  = await db_scalar("SELECT COUNT(*) FROM trades WHERE status = ?", "open")

    # Raw connection (for multi-statement transactions):
    async with get_db() as db:
        await db.execute("INSERT INTO bot_events ...")
        await db.commit()
"""

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

import aiosqlite

from bot.config import settings

_db_path: str = settings.db_path


async def init_db() -> None:
    """Read schema.sql and execute it against the configured database.

    Safe to call on every startup — all DDL uses IF NOT EXISTS and
    INSERT OR IGNORE, so existing data is never destroyed.
    """
    db_dir = os.path.dirname(_db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # schema.sql lives at the project root (one level above bot/)
    schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "schema.sql",
    )

    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        with open(schema_path, encoding="utf-8") as fh:
            await db.executescript(fh.read())
        await db.commit()


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager that yields an open aiosqlite connection.

    Rows are returned as aiosqlite.Row objects (subscriptable by column name).
    The connection is NOT auto-committed; callers must call ``await db.commit()``
    when they need to persist writes.
    """
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db


# ---------------------------------------------------------------------------
# Convenience helpers — each opens/closes its own connection.
# ---------------------------------------------------------------------------

async def db_fetchone(query: str, *args: Any) -> Optional[aiosqlite.Row]:
    """Execute *query* with positional *args* and return the first row or None."""
    async with get_db() as db:
        cur = await db.execute(query, args)
        return await cur.fetchone()


async def db_fetchall(query: str, *args: Any) -> list[aiosqlite.Row]:
    """Execute *query* with positional *args* and return all rows."""
    async with get_db() as db:
        cur = await db.execute(query, args)
        return await cur.fetchall()


async def db_execute(query: str, *args: Any) -> None:
    """Execute a write statement and commit immediately."""
    async with get_db() as db:
        await db.execute(query, args)
        await db.commit()


async def db_scalar(query: str, *args: Any) -> Any:
    """Execute *query* and return the first column of the first row, or None."""
    row = await db_fetchone(query, *args)
    if row is not None:
        return row[0]
    return None
