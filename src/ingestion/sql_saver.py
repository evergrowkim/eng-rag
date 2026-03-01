"""파싱된 블록을 SQLite에 저장.

엔지니어링 수치 데이터를 구조화하여 SQL 조회 가능하게 만든다.
- documents: 문서 메타데이터
- chunks: 모든 블록 공통 저장
- soil_parameters: 지반정수 테이블 → 구조화 저장
- section_checks: 검토결과 (OK/NG) 저장
"""

from __future__ import annotations

import uuid

import aiosqlite
from loguru import logger

from .layout_parser import BlockType, ParsedBlock, ParsedDocument


class SQLSaver:
    """파싱된 블록을 SQLite에 저장."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def save_document(self, doc: ParsedDocument, filename: str) -> str:
        """문서 저장 후 doc_id 반환."""
        doc_id = str(uuid.uuid4())

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")

            # 1. 문서 메타데이터 저장
            await db.execute(
                """
                INSERT INTO documents (id, filename, doc_type, project_name, page_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    filename,
                    "design_report",
                    doc.metadata.get("project_name"),
                    doc.page_count,
                ),
            )

            # 2. 블록 유형별 저장
            for block in doc.blocks:
                chunk_id = str(uuid.uuid4())

                # 공통: chunks 테이블
                await db.execute(
                    """
                    INSERT INTO chunks (id, doc_id, block_type, content, page_number)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        doc_id,
                        block.block_type.value,
                        block.content,
                        block.page,
                    ),
                )

                # 특수 처리
                if block.block_type == BlockType.SOIL_TABLE and block.table_data:
                    await self._save_soil_params(db, doc_id, block)
                elif block.block_type == BlockType.CHECK_RESULT and block.check_values:
                    await self._save_check_result(db, doc_id, block)

            await db.commit()

        logger.info(f"저장 완료: doc_id={doc_id}, {len(doc.blocks)}개 블록")
        return doc_id

    async def _save_soil_params(
        self, db: aiosqlite.Connection, doc_id: str, block: ParsedBlock
    ) -> None:
        """지반정수 테이블 → soil_parameters 테이블 저장."""
        for row in block.table_data or []:
            layer = row.get("지층", "").strip()
            if not layer:
                continue

            await db.execute(
                """
                INSERT INTO soil_parameters
                (doc_id, layer_name, N_value, unit_weight, cohesion,
                 friction_angle, kh, page_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    layer,
                    _safe_float(row.get("N치")),
                    _safe_float(row.get("단위중량")),
                    _safe_float(row.get("점착력")),
                    _safe_float(row.get("내부마찰각")),
                    _safe_float(row.get("수평지반반력계수")),
                    block.page,
                ),
            )

    async def _save_check_result(
        self, db: aiosqlite.Connection, doc_id: str, block: ParsedBlock
    ) -> None:
        """검토결과 → section_checks 테이블 저장."""
        cv = block.check_values or {}
        await db.execute(
            """
            INSERT INTO section_checks
            (doc_id, moment_calc, moment_allow, overall_result, page_number)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                cv.get("calculated"),
                cv.get("allowable"),
                cv.get("result"),
                block.page,
            ),
        )

    async def save_section_summary(
        self, db_path: str, doc_id: str, summary: dict
    ) -> None:
        """SectionAggregator가 추출한 단면 요약을 section_checks에 저장."""
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute(
                """
                INSERT INTO section_checks
                (doc_id, section_id, excavation_depth, surcharge_load,
                 moment_calc, moment_allow, embedment_SF,
                 head_disp_calc, head_disp_allow,
                 max_disp_calc, max_disp_allow, page_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    summary.get("section_id"),
                    summary.get("excavation_depth"),
                    summary.get("surcharge_load"),
                    summary.get("moment_calc"),
                    summary.get("moment_allow"),
                    summary.get("embedment_SF"),
                    summary.get("head_disp_calc"),
                    summary.get("head_disp_allow"),
                    summary.get("max_disp_calc"),
                    summary.get("max_disp_allow"),
                    summary.get("page"),
                ),
            )
            await db.commit()


def _safe_float(val: str | None) -> float | None:
    """문자열을 float으로 변환. 실패 시 None."""
    if val is None:
        return None
    try:
        return float(val.replace(",", ""))
    except (ValueError, AttributeError):
        return None
