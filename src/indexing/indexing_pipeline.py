"""인덱싱 파이프라인 통합.

VectorIndexer와 PageIndexer를 순차 실행하여
문서를 벡터 인덱스 + 계층 트리로 변환한다.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic
from loguru import logger
from openai import AsyncOpenAI
from qdrant_client import QdrantClient

from ..ingestion.layout_parser import ParsedDocument
from .page_indexer import PageIndexer
from .qdrant_setup import IndexingError, setup_collection
from .vector_indexer import VectorIndexer


class IndexingPipeline:
    """벡터 인덱싱 + PageIndex 트리 생성 파이프라인."""

    def __init__(
        self,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        index_dir: str = "data/indexes",
    ) -> None:
        logger.info(f"IndexingPipeline 초기화: qdrant={qdrant_host}:{qdrant_port}")

        try:
            qdrant_client = QdrantClient(host=qdrant_host, port=qdrant_port)
        except Exception as e:
            logger.error(f"Qdrant 연결 실패: {e}")
            raise IndexingError(str(e)) from e

        self.qdrant = setup_collection(qdrant_client)
        self.openai = AsyncOpenAI()
        self.anthropic = AsyncAnthropic()

        self.vector_indexer = VectorIndexer(self.qdrant, self.openai)
        self.page_indexer = PageIndexer(self.anthropic, index_dir=index_dir)

    async def index(self, doc: ParsedDocument, doc_id: str) -> dict[str, Any]:
        """문서 인덱싱 실행. 벡터 인덱싱 → 트리 생성."""
        logger.info(f"=== 인덱싱 시작: {doc_id} ===")

        try:
            # 1. 벡터 인덱싱
            point_count = await self.vector_indexer.index_document(doc, doc_id)

            # 2. PageIndex 트리 생성
            tree = await self.page_indexer.build_tree(doc, doc_id)

            result: dict[str, Any] = {
                "doc_id": doc_id,
                "vector_points": point_count,
                "tree_nodes": len(tree.get("tree", [])),
            }
            logger.info(f"=== 인덱싱 완료: {doc_id} — {result} ===")
            return result

        except IndexingError:
            raise
        except Exception as e:
            logger.error(f"인덱싱 중 예상치 못한 오류: {e}")
            raise IndexingError(str(e)) from e
