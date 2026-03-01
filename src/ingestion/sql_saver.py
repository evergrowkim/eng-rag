"""파싱된 블록을 SQLite에 저장.

엔지니어링 수치 데이터를 구조화하여 SQL 조회 가능하게 만든다.
- documents: 문서 메타데이터
- chunks: 모든 블록 공통 저장
- soil_parameters: 지반정수 테이블 → 구조화 저장
- section_checks: 검토결과 (OK/NG) 저장
- anchor_design: 앵커/지보재 설계값
"""

from __future__ import annotations

import uuid

import aiosqlite
from loguru import logger

from .layout_parser import BlockType, ParsedBlock, ParsedDocument

# 지반정수 테이블 헤더 매핑 (실제 PDF에서 다양한 형태로 등장)
SOIL_HEADER_MAP: dict[str, str] = {
    # N값
    "N치": "N_value",
    "N값": "N_value",
    "N": "N_value",
    "SPT-N": "N_value",
    # 단위중량
    "단위중량": "unit_weight",
    "γt": "unit_weight",
    "rt": "unit_weight",
    "단위\n중량": "unit_weight",
    "단위 중량": "unit_weight",
    "γ": "unit_weight",
    # 점착력
    "점착력": "cohesion",
    "c": "cohesion",
    "C": "cohesion",
    "c(kN/m²)": "cohesion",
    "점착력\n(kN/m²)": "cohesion",
    # 내부마찰각
    "내부마찰각": "friction_angle",
    "φ": "friction_angle",
    "Φ": "friction_angle",
    "내부\n마찰각": "friction_angle",
    "내부 마찰각": "friction_angle",
    # 수평지반반력계수
    "수평지반반력계수": "kh",
    "Kh": "kh",
    "kh": "kh",
    "수평반력계수": "kh",
    "수평\n지반반력\n계수": "kh",
    # 지층명
    "지층": "layer_name",
    "지층명": "layer_name",
    "지 층": "layer_name",
    "토질": "layer_name",
    "지반종류": "layer_name",
}


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
            # 정규화된 키로 변환
            normalized = self._normalize_soil_row(row)

            layer = normalized.get("layer_name", "").strip()
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
                    _safe_float(normalized.get("N_value")),
                    _safe_float(normalized.get("unit_weight")),
                    _safe_float(normalized.get("cohesion")),
                    _safe_float(normalized.get("friction_angle")),
                    _safe_float(normalized.get("kh")),
                    block.page,
                ),
            )

    def _normalize_soil_row(self, row: dict[str, str]) -> dict[str, str]:
        """지반정수 행의 다양한 헤더 형태를 정규화."""
        normalized: dict[str, str] = {}
        for key, value in row.items():
            clean_key = key.strip().replace("\n", "").replace(" ", "")
            # 정확한 매핑 검색
            mapped = SOIL_HEADER_MAP.get(key)
            if not mapped:
                mapped = SOIL_HEADER_MAP.get(clean_key)
            if not mapped:
                # 부분 매칭 시도
                for header, target in SOIL_HEADER_MAP.items():
                    if header in clean_key or clean_key in header:
                        mapped = target
                        break
            if mapped:
                normalized[mapped] = value
            else:
                normalized[key] = value
        return normalized

    async def _save_check_result(
        self, db: aiosqlite.Connection, doc_id: str, block: ParsedBlock
    ) -> None:
        """검토결과 → section_checks 테이블 저장."""
        cv = block.check_values or {}

        # block.content에서 section_id 추출 시도
        import re
        section_id = None
        sec_match = re.search(r"SEC-(\w+(?:\([^)]*\))?)", block.content)
        if sec_match:
            section_id = f"SEC-{sec_match.group(1)}"

        await db.execute(
            """
            INSERT INTO section_checks
            (doc_id, section_id, moment_calc, moment_allow, overall_result, page_number)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                section_id,
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
                 moment_calc, moment_allow,
                 embedment_depth, embedment_SF, embedment_SF_allow,
                 head_disp_calc, head_disp_allow,
                 max_disp_calc, max_disp_allow,
                 overall_result, page_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    summary.get("section_id"),
                    summary.get("excavation_depth"),
                    summary.get("surcharge_load"),
                    summary.get("moment_calc"),
                    summary.get("moment_allow"),
                    summary.get("embedment_depth"),
                    summary.get("embedment_SF"),
                    summary.get("embedment_SF_allow"),
                    summary.get("head_disp_calc"),
                    summary.get("head_disp_allow"),
                    summary.get("max_disp_calc"),
                    summary.get("max_disp_allow"),
                    summary.get("overall_result"),
                    summary.get("page"),
                ),
            )

            # anchor_design 저장
            anchor_info = summary.get("anchor_info")
            if anchor_info:
                await db.execute(
                    """
                    INSERT INTO anchor_design
                    (doc_id, section_id, stage, free_length, anchor_length,
                     design_force, tensile_force, usage_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        summary.get("section_id"),
                        anchor_info.get("stage"),
                        anchor_info.get("free_length"),
                        anchor_info.get("anchor_length"),
                        anchor_info.get("design_force"),
                        anchor_info.get("tensile_force"),
                        "TEMPORARY",
                    ),
                )

            await db.commit()


def _safe_float(val: str | None) -> float | None:
    """문자열을 float으로 변환. 실패 시 None."""
    if val is None:
        return None
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, AttributeError):
        return None
