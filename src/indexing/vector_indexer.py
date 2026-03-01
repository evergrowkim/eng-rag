"""벡터 인덱서.

파싱된 블록을 임베딩하여 Qdrant에 저장한다.
임베딩 모델: text-embedding-3-large (3072차원)
배치 크기: 100 (API 제한 고려)
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from ..ingestion.layout_parser import BlockType, ParsedBlock, ParsedDocument
from .qdrant_setup import COLLECTION_NAME, IndexingError

BATCH_SIZE: int = 100
EMBEDDING_MODEL: str = "text-embedding-3-large"
CONTENT_MAX_LEN: int = 1500  # payload content 최대 길이

# SEC-XY 패턴 (검토 요약 페이지에서 section_id 추출용)
SEC_ID_RE = re.compile(r"SEC-(\w+(?:\([^)]*\))?)")


class VectorIndexer:
    """파싱된 블록을 임베딩하여 Qdrant에 저장."""

    def __init__(self, qdrant_client: QdrantClient, openai_client: AsyncOpenAI) -> None:
        self.qdrant = qdrant_client
        self.openai = openai_client
        self.collection = COLLECTION_NAME

    async def index_document(
        self,
        doc: ParsedDocument,
        doc_id: str,
        index_dir: str = "data/indexes",
    ) -> int:
        """문서의 모든 블록을 인덱싱. 저장된 포인트 수 반환."""
        # PageIndex 트리에서 page → section_id 매핑 로드
        page_to_section = self._load_page_section_map(doc_id, index_dir)

        chunks = self._prepare_chunks(doc, doc_id, page_to_section)

        if not chunks:
            logger.warning(f"인덱싱할 청크 없음: {doc_id}")
            return 0

        logger.info(f"인덱싱 시작: {len(chunks)}개 청크")

        total = 0
        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i : i + BATCH_SIZE]
            texts = [c["text"] for c in batch]

            try:
                # 임베딩 생성
                response = await self.openai.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=texts,
                )
                vectors = [r.embedding for r in response.data]

                # Qdrant 저장
                points = [
                    PointStruct(
                        id=chunk["id"],
                        vector=vector,
                        payload=chunk["payload"],
                    )
                    for chunk, vector in zip(batch, vectors, strict=True)
                ]

                self.qdrant.upsert(
                    collection_name=self.collection,
                    points=points,
                )
                total += len(points)
                logger.debug(f"  배치 {i // BATCH_SIZE + 1}: {len(points)}개 저장")

            except Exception as e:
                logger.error(f"임베딩 배치 실패 (offset={i}): {e}")
                raise IndexingError(str(e)) from e

        logger.info(f"인덱싱 완료: {total}개 포인트")
        return total

    def _prepare_chunks(
        self,
        doc: ParsedDocument,
        doc_id: str,
        page_to_section: dict[int, str],
    ) -> list[dict[str, Any]]:
        """블록을 청크로 변환. 유형별 다른 전략 적용."""
        chunks: list[dict[str, Any]] = []

        for block in doc.blocks:
            chunk_text = self._prepare_chunk_text(block)
            if not chunk_text.strip():
                continue

            # section_id: 트리 매핑 → 블록 content에서 추출 → None
            section_id = page_to_section.get(block.page)
            if not section_id:
                m = SEC_ID_RE.search(block.content)
                if m:
                    section_id = f"SEC-{m.group(1)}"

            payload: dict[str, Any] = {
                "doc_id": doc_id,
                "filename": doc.filename,
                "block_type": block.block_type.value,
                "page_number": block.page,
                "content": block.content[:CONTENT_MAX_LEN],
                "has_table": block.table_data is not None,
                "check_result": block.check_values,
                "section_id": section_id,
            }

            chunks.append({
                "id": str(uuid.uuid4()),
                "text": chunk_text,
                "payload": payload,
            })

            # SOIL_TABLE: 행 단위 추가 청크
            if block.block_type == BlockType.SOIL_TABLE:
                for row in block.table_data or []:
                    row_text = self._soil_row_to_sentence(row)
                    if row_text:
                        chunks.append({
                            "id": str(uuid.uuid4()),
                            "text": row_text,
                            "payload": {**payload, "is_row_chunk": True},
                        })

        return chunks

    def _load_page_section_map(
        self, doc_id: str, index_dir: str
    ) -> dict[int, str]:
        """PageIndex 트리 JSON에서 page → section_id 매핑 생성."""
        tree_path = Path(index_dir) / f"{doc_id}_tree.json"
        if not tree_path.exists():
            logger.debug(f"트리 파일 없음: {tree_path}")
            return {}

        try:
            with open(tree_path, encoding="utf-8") as f:
                tree_data = json.load(f)
        except Exception as e:
            logger.warning(f"트리 파일 로드 실패: {e}")
            return {}

        mapping: dict[int, str] = {}
        self._walk_tree_for_sections(tree_data.get("tree", []), mapping)
        logger.info(f"페이지-섹션 매핑 {len(mapping)}건 로드")
        return mapping

    def _walk_tree_for_sections(
        self, nodes: list[dict], mapping: dict[int, str]
    ) -> None:
        """트리 노드를 재귀 탐색하여 CHECK_SECTION 페이지에 section_id 매핑."""
        for node in nodes:
            if node.get("node_type") == "CHECK_SECTION":
                title = node.get("title", "")
                m = SEC_ID_RE.search(title)
                if m:
                    sec_id = f"SEC-{m.group(1)}"
                    for page in node.get("pages", []):
                        mapping[page] = sec_id
            # 자식 노드 탐색
            self._walk_tree_for_sections(node.get("children", []), mapping)

    def _prepare_chunk_text(self, block: ParsedBlock) -> str:
        """검색에 최적화된 텍스트 생성.

        임베딩할 텍스트는 자연어에 가까울수록 좋음.
        """
        if block.block_type == BlockType.SOIL_TABLE:
            return self._soil_table_to_natural(block)

        if block.block_type == BlockType.CHECK_RESULT:
            cv = block.check_values or {}
            return (
                f"검토결과: 계산값 {cv.get('calculated')} "
                f"허용값 {cv.get('allowable')} "
                f"결과 {cv.get('result')} "
                f"(활용률 {cv.get('utilization', 'N/A')})"
            )

        return block.content

    def _soil_table_to_natural(self, block: ParsedBlock) -> str:
        """지반정수 테이블을 자연어로 변환."""
        lines = ["지반정수 요약표:"]
        for row in block.table_data or []:
            layer = row.get("지층", "")
            n = row.get("N치", "")
            gamma = row.get("단위중량", "")
            c = row.get("점착력", "")
            phi = row.get("내부마찰각", "")
            kh = row.get("수평지반반력계수", "")
            lines.append(
                f"{layer}: N치={n}, 단위중량={gamma}kN/m³, "
                f"점착력={c}kN/m², 내부마찰각={phi}°, 수평반력계수={kh}kN/m³"
            )
        return "\n".join(lines)

    def _soil_row_to_sentence(self, row: dict[str, str]) -> str:
        """개별 지반정수 행을 문장으로 변환."""
        layer = row.get("지층", "")
        if not layer:
            return ""
        parts: list[str] = []
        for key, unit in [
            ("N치", ""),
            ("단위중량", "kN/m³"),
            ("점착력", "kN/m²"),
            ("내부마찰각", "°"),
        ]:
            val = row.get(key, "")
            if val:
                parts.append(f"{key} {val}{unit}")
        return f"{layer} 지층의 지반정수: " + ", ".join(parts)
