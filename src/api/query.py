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


class TreeContext(BaseModel):
    matched_nodes: list[str] = []
    pages: list[int] = []
    section_ids: list[str] = []


class QueryResponse(BaseModel):
    query: str
    query_type: str
    answer: str
    sources: list[dict]
    sql_query: str | None = None
    vector_count: int = 0
    tree_context: TreeContext | None = None


@router.post("/", response_model=QueryResponse)
async def ask(req: QueryRequest) -> QueryResponse:
    """자연어 질문 처리."""
    logger.info(f"질문: {req.query}")

    result = await _get_engine().search(req.query, req.doc_ids)

    # 트리 결과를 TreeContext로 변환
    tree_ctx = None
    if result.tree_results:
        tree_ctx = TreeContext(
            matched_nodes=[tr.get("title", "") for tr in result.tree_results],
            pages=sorted({
                p for tr in result.tree_results
                for p in tr.get("pages", [])
            }),
            section_ids=[
                tr["section_id"]
                for tr in result.tree_results
                if tr.get("section_id")
            ],
        )

    return QueryResponse(
        query=req.query,
        query_type=result.query_type,
        answer=result.answer,
        sources=result.sources,
        sql_query=result.sql_result.get("sql") if result.sql_result else None,
        vector_count=len(result.vector_results),
        tree_context=tree_ctx,
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
