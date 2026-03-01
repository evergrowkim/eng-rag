"""문서 업로드 / 목록 / 삭제 라우터."""

from __future__ import annotations

import shutil
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from ..ingestion.layout_parser import LayoutParser
from ..ingestion.pipeline import IngestionPipeline

router = APIRouter(tags=["documents"])

UPLOAD_DIR = Path("data/uploads")
DB_PATH = "data/db/doaz.db"

ingestion = IngestionPipeline(DB_PATH)

# Lazy init — Qdrant가 실행 중이지 않아도 서버 기동 가능
_indexing = None


def _get_indexing():
    global _indexing
    if _indexing is None:
        from ..indexing.indexing_pipeline import IndexingPipeline
        _indexing = IndexingPipeline()
    return _indexing


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    page_count: int
    block_count: int
    table_count: int
    vector_points: int
    status: str


@router.post("/upload", response_model=DocumentInfo)
async def upload_document(file: UploadFile = File(...)) -> DocumentInfo:
    """PDF 업로드 → 파싱 → 인덱싱. 전체 과정 자동 실행."""
    if not file.filename or not file.filename.endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 업로드 가능합니다")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    save_path = UPLOAD_DIR / file.filename

    # 저장
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    logger.info(f"업로드: {file.filename} ({save_path.stat().st_size // 1024}KB)")

    try:
        # 파싱 + DB 저장
        ingest_result = await ingestion.ingest(str(save_path))
        doc_id = ingest_result["doc_id"]

        # 벡터 인덱싱 (파싱 결과 재사용)
        parser = LayoutParser()
        parsed_doc = parser.parse(str(save_path))
        index_result = await _get_indexing().index(parsed_doc, doc_id)

        return DocumentInfo(
            doc_id=doc_id,
            filename=file.filename,
            page_count=ingest_result["page_count"],
            block_count=ingest_result["block_count"],
            table_count=ingest_result["table_count"],
            vector_points=index_result["vector_points"],
            status="indexed",
        )

    except Exception as e:
        logger.error(f"처리 실패: {e}")
        raise HTTPException(500, f"문서 처리 실패: {e!s}")


@router.get("/")
async def list_documents() -> list[dict]:
    """업로드된 문서 목록."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, filename, project_name, page_count, uploaded_at "
            "FROM documents ORDER BY uploaded_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


@router.delete("/{doc_id}")
async def delete_document(doc_id: str) -> dict:
    """문서 및 모든 인덱스 삭제."""
    # DB 삭제
    async with aiosqlite.connect(DB_PATH) as db:
        for table in [
            "soil_parameters",
            "section_checks",
            "anchor_design",
            "material_allowables",
            "chunks",
        ]:
            await db.execute(f"DELETE FROM {table} WHERE doc_id = ?", (doc_id,))
        await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await db.commit()

    # Vector 삭제
    qdrant = QdrantClient(host="localhost", port=6333)
    qdrant.delete(
        collection_name="doaz_eng_rag",
        points_selector=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        ),
    )

    return {"status": "deleted", "doc_id": doc_id}
