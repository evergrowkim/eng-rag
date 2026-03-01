"""헬스체크 라우터."""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter
from qdrant_client import QdrantClient

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    status: dict = {"status": "ok"}

    # Qdrant 확인
    try:
        qdrant = QdrantClient(host="localhost", port=6333)
        qdrant.get_collections()
        status["qdrant"] = "connected"
    except Exception:
        status["qdrant"] = "error"
        status["status"] = "degraded"

    # SQLite 확인
    try:
        async with aiosqlite.connect("data/db/doaz.db") as db:
            await db.execute("SELECT 1")
        status["db"] = "connected"
    except Exception:
        status["db"] = "error"
        status["status"] = "degraded"

    return status
