"""Database initialization script.

Usage:
    uv run python scripts/init_db.py
"""

import asyncio
from pathlib import Path

import aiosqlite


DB_PATH = Path("data/db/doaz.db")
SCHEMA_PATH = Path("docs/schema.sql")


async def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(schema)
        await db.commit()

    print(f"Database initialized: {DB_PATH}")


if __name__ == "__main__":
    asyncio.run(init())
