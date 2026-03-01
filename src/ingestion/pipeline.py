"""PDF 업로드부터 DB 저장까지 전체 인제스션 파이프라인.

사용법:
    pipeline = IngestionPipeline(db_path="data/db/doaz.db")
    result = await pipeline.ingest("path/to/report.pdf")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from .block_classifier import BlockClassifier
from .layout_parser import BlockType, LayoutParser, ParsedDocument
from .section_aggregator import SectionAggregator
from .sql_saver import SQLSaver


class IngestionPipeline:
    """PDF 업로드부터 DB 저장까지 전체 파이프라인."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.parser = LayoutParser()
        self.classifier = BlockClassifier()
        self.saver = SQLSaver(db_path)
        self.aggregator = SectionAggregator()

    async def ingest(
        self, pdf_path: str
    ) -> tuple[dict[str, Any], ParsedDocument]:
        """PDF를 파싱하고 DB에 저장한다.

        Returns:
            (result_dict, parsed_document) 튜플.
            parsed_document는 인덱싱 파이프라인에 전달하여 이중 파싱을 방지한다.
        """
        filename = Path(pdf_path).name
        logger.info(f"=== 인제스션 시작: {filename} ===")

        # 1. 파싱
        doc = self.parser.parse(pdf_path)

        # 2. 블록 재분류
        doc.blocks = self.classifier.reclassify_blocks(doc.blocks)

        # 3. 단면 요약 추출
        section_summaries = self.aggregator.aggregate_from_blocks(doc.blocks)

        # 4. SQL 저장
        doc_id = await self.saver.save_document(doc, filename)

        # 5. 단면 요약 저장
        for summary in section_summaries:
            await self.saver.save_section_summary(self.db_path, doc_id, summary)

        # 통계 집계
        table_count = sum(1 for b in doc.blocks if "table" in b.block_type.value)
        check_count = sum(
            1 for b in doc.blocks if b.block_type == BlockType.CHECK_RESULT
        )

        logger.info(f"=== 인제스션 완료: {doc_id} ===")

        result = {
            "doc_id": doc_id,
            "page_count": doc.page_count,
            "block_count": len(doc.blocks),
            "table_count": table_count,
            "check_count": check_count,
            "section_count": len(section_summaries),
            "metadata": doc.metadata,
        }

        return result, doc
