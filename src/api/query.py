"""질문 처리 라우터."""

from __future__ import annotations

import aiosqlite
from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

router = APIRouter(tags=["query"])

# Lazy init — Qdrant/OpenAI 없이도 서버 기동 가능
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from ..retrieval.search_engine import SearchEngine
        _engine = SearchEngine(db_path="data/db/doaz.db")
    return _engine


class QueryRequest(BaseModel):
    query: str
    doc_ids: list[str] | None = None


class QueryResponse(BaseModel):
    query: str
    query_type: str
    answer: str
    sources: list[dict]
    sql_query: str | None = None
    vector_count: int = 0


@router.post("/", response_model=QueryResponse)
async def ask(req: QueryRequest) -> QueryResponse:
    """자연어 질문 처리."""
    logger.info(f"질문: {req.query}")

    result = await _get_engine().search(req.query, req.doc_ids)

    return QueryResponse(
        query=req.query,
        query_type=result.query_type,
        answer=result.answer,
        sources=result.sources,
        sql_query=result.sql_result.get("sql") if result.sql_result else None,
        vector_count=len(result.vector_results),
    )


@router.post("/sql")
async def direct_sql(body: dict) -> dict:
    """직접 SQL 실행 (개발/디버깅용)."""
    sql = body.get("sql", "")
    async with aiosqlite.connect("data/db/doaz.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql) as cursor:
            rows = await cursor.fetchall()
    return {"rows": [dict(r) for r in rows]}
