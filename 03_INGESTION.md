# 03. 문서 파싱 (Ingestion Pipeline)

## Phase 1 구현 목표
PDF 업로드 → 레이아웃 인식 파싱 → SQL 저장까지 완성

---

## 1. LayoutParser 구현

```python
# src/ingestion/layout_parser.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any
import pdfplumber
import fitz  # PyMuPDF
import re
from loguru import logger


class BlockType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    CHECK_RESULT = "check_result"
    SOIL_TABLE = "soil_table"
    SUNEX_OUTPUT = "sunex_output"
    FIGURE = "figure"


@dataclass
class ParsedBlock:
    block_type: BlockType
    content: str
    page: int
    bbox: tuple[float, float, float, float] | None = None
    table_data: list[dict[str, Any]] | None = None
    check_values: dict[str, Any] | None = None
    raw_rows: list[list[str]] | None = None


@dataclass
class ParsedDocument:
    filename: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class LayoutParser:
    """
    엔지니어링 PDF 레이아웃 인식 파서.
    
    처리 순서:
    1. pdfplumber로 테이블 먼저 감지 (테이블은 좌표로 예약)
    2. 나머지 영역에서 텍스트 블록 추출
    3. 각 블록 유형 분류
    4. 특수 패턴 (검토결과, 지반정수, SUNEX) 처리
    """

    # 패턴 정의
    CHECK_RESULT_RE = re.compile(
        r"(\d+\.?\d*)\s*[<>]\s*(\d+\.?\d*)\s*(O\.?K|N\.?G)", re.IGNORECASE
    )
    SOIL_HEADERS = {"N치", "단위중량", "점착력", "내부마찰각", "수평지반반력계수"}
    SUNEX_MARKERS = {"SUNEX", "Step No.", "EXCA TO", "kN/ea"}

    def parse(self, pdf_path: str) -> ParsedDocument:
        logger.info(f"파싱 시작: {pdf_path}")
        doc = ParsedDocument(filename=pdf_path)

        with pdfplumber.open(pdf_path) as pdf:
            doc.page_count = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages, start=1):
                logger.debug(f"  페이지 {page_num}/{doc.page_count} 처리 중")

                # 1. 테이블 추출
                table_blocks = self._extract_tables(page, page_num)
                doc.blocks.extend(table_blocks)

                # 2. 텍스트 추출 (테이블 영역 제외)
                text_blocks = self._extract_text_blocks(page, page_num, table_blocks)
                doc.blocks.extend(text_blocks)

        # 3. 메타데이터 추출
        doc.metadata = self._extract_metadata(doc.blocks)

        logger.info(f"파싱 완료: {len(doc.blocks)}개 블록, {doc.page_count}페이지")
        return doc

    def _extract_tables(self, page, page_num: int) -> list[ParsedBlock]:
        """테이블을 구조화 형태로 추출."""
        blocks = []
        tables = page.extract_tables()

        for table in tables:
            if not table or len(table) < 2:
                continue

            # 헤더 추출 (첫 행)
            headers = [str(cell or "").strip() for cell in table[0]]

            # 지반정수 테이블 감지
            block_type = BlockType.TABLE
            if self._is_soil_table(headers):
                block_type = BlockType.SOIL_TABLE

            # 딕셔너리 형태로 변환
            rows = []
            for row in table[1:]:
                row_dict = {}
                for i, cell in enumerate(row):
                    if i < len(headers):
                        row_dict[headers[i]] = str(cell or "").strip()
                rows.append(row_dict)

            # 텍스트 표현 생성
            content = self._table_to_text(headers, rows)

            blocks.append(ParsedBlock(
                block_type=block_type,
                content=content,
                page=page_num,
                table_data=rows,
                raw_rows=table
            ))

        return blocks

    def _extract_text_blocks(
        self, page, page_num: int, table_blocks: list[ParsedBlock]
    ) -> list[ParsedBlock]:
        """텍스트 블록 추출 및 분류."""
        blocks = []
        text = page.extract_text() or ""

        if not text.strip():
            return blocks

        # SUNEX 출력 감지
        if any(marker in text for marker in self.SUNEX_MARKERS):
            return [ParsedBlock(
                block_type=BlockType.SUNEX_OUTPUT,
                content=text,
                page=page_num
            )]

        # 검토결과 패턴 감지
        check_blocks = self._extract_check_results(text, page_num)
        blocks.extend(check_blocks)

        # 일반 텍스트 (섹션 단위로 분리)
        text_block = ParsedBlock(
            block_type=BlockType.TEXT,
            content=text,
            page=page_num
        )
        blocks.append(text_block)

        return blocks

    def _extract_check_results(self, text: str, page_num: int) -> list[ParsedBlock]:
        """
        "179.83 < 270.32 O.K" 형태의 검토결과 추출.
        """
        blocks = []
        for match in self.CHECK_RESULT_RE.finditer(text):
            calc_val = float(match.group(1))
            allow_val = float(match.group(2))
            result = match.group(3).replace(".", "")  # OK or NG

            blocks.append(ParsedBlock(
                block_type=BlockType.CHECK_RESULT,
                content=match.group(0),
                page=page_num,
                check_values={
                    "calculated": calc_val,
                    "allowable": allow_val,
                    "result": result,
                    "utilization": round(calc_val / allow_val, 3) if allow_val else None
                }
            ))

        return blocks

    def _is_soil_table(self, headers: list[str]) -> bool:
        """지반정수 테이블 여부 판별."""
        header_set = {h for h in headers if h}
        return len(header_set & self.SOIL_HEADERS) >= 3

    def _table_to_text(self, headers: list[str], rows: list[dict]) -> str:
        """테이블을 검색 가능한 텍스트로 변환."""
        lines = [" | ".join(headers)]
        for row in rows:
            lines.append(" | ".join(str(row.get(h, "")) for h in headers))
        return "\n".join(lines)

    def _extract_metadata(self, blocks: list[ParsedBlock]) -> dict[str, Any]:
        """텍스트 블록에서 메타데이터 추출."""
        metadata: dict[str, Any] = {
            "project_name": None,
            "referenced_standards": [],
        }

        full_text = " ".join(b.content for b in blocks if b.block_type == BlockType.TEXT)

        # 프로젝트명 추출 (제목 패턴)
        project_patterns = [
            r"([가-힣\s]+(?:재개발|신축|증축|공사|사업))",
        ]
        for pattern in project_patterns:
            match = re.search(pattern, full_text)
            if match:
                metadata["project_name"] = match.group(1).strip()
                break

        # 기준서 참조 추출
        standards = re.findall(r"KDS[\s\d]+|KCS[\s\d]+|ACI[\s\d-]+|ASME[\s\w-]+", full_text)
        metadata["referenced_standards"] = list(set(standards))

        return metadata
```

---

## 2. SQL 저장 레이어

```python
# src/ingestion/sql_saver.py

import uuid
import json
import re
from loguru import logger
import aiosqlite

from .layout_parser import ParsedDocument, ParsedBlock, BlockType


class SQLSaver:
    """
    파싱된 블록을 SQLite에 저장.
    엔지니어링 수치 데이터를 구조화하여 SQL 조회 가능하게 만듦.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def save_document(self, doc: ParsedDocument, filename: str) -> str:
        """문서 저장 후 doc_id 반환."""
        doc_id = str(uuid.uuid4())

        async with aiosqlite.connect(self.db_path) as db:
            # 1. 문서 메타데이터 저장
            await db.execute("""
                INSERT INTO documents (id, filename, doc_type, project_name, page_count)
                VALUES (?, ?, ?, ?, ?)
            """, (
                doc_id, filename, "design_report",
                doc.metadata.get("project_name"),
                doc.page_count
            ))

            # 2. 블록 유형별 저장
            for block in doc.blocks:
                chunk_id = str(uuid.uuid4())

                # 공통: chunks 테이블
                await db.execute("""
                    INSERT INTO chunks (id, doc_id, block_type, content, page_number)
                    VALUES (?, ?, ?, ?, ?)
                """, (chunk_id, doc_id, block.block_type.value,
                      block.content, block.page))

                # 특수 처리
                if block.block_type == BlockType.SOIL_TABLE and block.table_data:
                    await self._save_soil_params(db, doc_id, block)

                elif block.block_type == BlockType.CHECK_RESULT and block.check_values:
                    await self._save_check_result(db, doc_id, block)

            await db.commit()

        logger.info(f"저장 완료: doc_id={doc_id}")
        return doc_id

    async def _save_soil_params(self, db, doc_id: str, block: ParsedBlock):
        """지반정수 테이블 → soil_parameters 테이블 저장."""
        for row in (block.table_data or []):
            layer = row.get("지층", "").strip()
            if not layer:
                continue

            def safe_float(val: str) -> float | None:
                try:
                    return float(val.replace(",", ""))
                except (ValueError, AttributeError):
                    return None

            await db.execute("""
                INSERT INTO soil_parameters
                (doc_id, layer_name, N_value, unit_weight, cohesion, friction_angle, kh, page_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, layer,
                safe_float(row.get("N치")),
                safe_float(row.get("단위중량")),
                safe_float(row.get("점착력")),
                safe_float(row.get("내부마찰각")),
                safe_float(row.get("수평지반반력계수")),
                block.page
            ))

    async def _save_check_result(self, db, doc_id: str, block: ParsedBlock):
        """검토결과 → section_checks 테이블 저장 (부분 저장)."""
        # 전체 단면정보는 별도 로직으로 집계 필요
        # 여기서는 개별 검토값만 저장
        cv = block.check_values or {}
        await db.execute("""
            INSERT INTO section_checks
            (doc_id, moment_calc, moment_allow, overall_result, page_number)
            VALUES (?, ?, ?, ?, ?)
        """, (
            doc_id,
            cv.get("calculated"),
            cv.get("allowable"),
            cv.get("result"),
            block.page
        ))
```

---

## 3. 섹션 집계기 (Section Aggregator)

```python
# src/ingestion/section_aggregator.py

import re
from loguru import logger


class SectionAggregator:
    """
    단면 검토 요약 섹션(SEC-1O, SEC-2A 등)을 파싱하여
    구조화된 데이터로 변환.
    
    패턴 예시:
    "4-2-17 SEC-1O 검토 요약"
    → section_id = "SEC-1O"
    → excavation_depth = 9.48
    → anchor_count = 4
    """

    SECTION_RE = re.compile(r"SEC-(\w+)")
    DEPTH_RE = re.compile(r"굴착깊이\s*:\s*(\d+\.?\d*)\s*m")
    SURCHARGE_RE = re.compile(r"상재하중\s*:\s*[\w가-힣()]*\s*\(?(\d+\.?\d*)\s*kN/m")

    def extract_section_summary(self, text: str, page: int) -> dict | None:
        """텍스트에서 단면 검토 요약 추출."""
        sec_match = self.SECTION_RE.search(text)
        if not sec_match:
            return None

        section_id = f"SEC-{sec_match.group(1)}"

        depth_match = self.DEPTH_RE.search(text)
        surcharge_match = self.SURCHARGE_RE.search(text)

        # 부재 검토 결과 추출
        moment_calc, moment_allow = self._extract_check_pair(text, "휨모멘트")
        embed_sf = self._extract_safety_factor(text)
        head_disp, head_disp_allow = self._extract_displacement(text, "두부변위")
        max_disp, max_disp_allow = self._extract_displacement(text, "벽체변위")

        return {
            "section_id": section_id,
            "page": page,
            "excavation_depth": float(depth_match.group(1)) if depth_match else None,
            "surcharge_load": float(surcharge_match.group(1)) if surcharge_match else None,
            "moment_calc": moment_calc,
            "moment_allow": moment_allow,
            "embedment_SF": embed_sf,
            "head_disp_calc": head_disp,
            "head_disp_allow": head_disp_allow,
            "max_disp_calc": max_disp,
            "max_disp_allow": max_disp_allow,
        }

    def _extract_check_pair(
        self, text: str, keyword: str
    ) -> tuple[float | None, float | None]:
        pattern = re.compile(rf"{keyword}.*?(\d+\.?\d+)\s*<\s*(\d+\.?\d+)")
        m = pattern.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))
        return None, None

    def _extract_safety_factor(self, text: str) -> float | None:
        m = re.search(r"안전율\s+(\d+\.?\d+)", text)
        return float(m.group(1)) if m else None

    def _extract_displacement(
        self, text: str, keyword: str
    ) -> tuple[float | None, float | None]:
        m = re.search(rf"{keyword}.*?(\d+\.?\d+)\s*mm.*?(\d+\.?\d+)\s*mm", text)
        if m:
            return float(m.group(1)), float(m.group(2))
        return None, None
```

---

## 4. 파싱 파이프라인 통합

```python
# src/ingestion/pipeline.py

from pathlib import Path
from loguru import logger

from .layout_parser import LayoutParser
from .sql_saver import SQLSaver
from .section_aggregator import SectionAggregator


class IngestionPipeline:
    """
    PDF 업로드부터 DB 저장까지 전체 파이프라인.
    """

    def __init__(self, db_path: str):
        self.parser = LayoutParser()
        self.saver = SQLSaver(db_path)
        self.aggregator = SectionAggregator()

    async def ingest(self, pdf_path: str) -> dict:
        """
        Returns:
            {
                "doc_id": str,
                "page_count": int,
                "block_count": int,
                "table_count": int,
                "section_count": int,
                "soil_layer_count": int
            }
        """
        filename = Path(pdf_path).name
        logger.info(f"=== 인제스션 시작: {filename} ===")

        # 1. 파싱
        doc = self.parser.parse(pdf_path)

        # 2. SQL 저장
        doc_id = await self.saver.save_document(doc, filename)

        # 통계 집계
        table_count = sum(1 for b in doc.blocks if "table" in b.block_type.value)

        logger.info(f"=== 인제스션 완료: {doc_id} ===")

        return {
            "doc_id": doc_id,
            "page_count": doc.page_count,
            "block_count": len(doc.blocks),
            "table_count": table_count,
            "metadata": doc.metadata,
        }
```

---

## 5. 파싱 결과 검증 CLI

```python
# scripts/verify_parse.py
"""
사용법: uv run python scripts/verify_parse.py path/to/file.pdf
"""

import asyncio
import sys
import json
from src.ingestion.pipeline import IngestionPipeline
from src.ingestion.layout_parser import LayoutParser


async def verify(pdf_path: str):
    # 파싱만 (DB 저장 없이)
    parser = LayoutParser()
    doc = parser.parse(pdf_path)

    print(f"\n{'='*60}")
    print(f"파일: {pdf_path}")
    print(f"페이지 수: {doc.page_count}")
    print(f"전체 블록 수: {len(doc.blocks)}")
    print(f"메타데이터: {json.dumps(doc.metadata, ensure_ascii=False, indent=2)}")

    # 블록 유형별 통계
    from collections import Counter
    type_counts = Counter(b.block_type.value for b in doc.blocks)
    print(f"\n블록 유형별 분포:")
    for btype, count in type_counts.items():
        print(f"  {btype}: {count}개")

    # 지반정수 테이블 미리보기
    soil_blocks = [b for b in doc.blocks if b.block_type.value == "soil_table"]
    if soil_blocks:
        print(f"\n지반정수 테이블 ({len(soil_blocks)}개):")
        for b in soil_blocks[:2]:
            print(f"  페이지 {b.page}:")
            for row in (b.table_data or [])[:3]:
                print(f"    {row}")

    # 검토결과 미리보기
    check_blocks = [b for b in doc.blocks if b.block_type.value == "check_result"]
    if check_blocks:
        print(f"\n검토결과 ({len(check_blocks)}개):")
        for b in check_blocks[:5]:
            cv = b.check_values or {}
            print(f"  p.{b.page}: {cv.get('calculated')} < {cv.get('allowable')} → {cv.get('result')}")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/uploads/sample.pdf"
    asyncio.run(verify(path))
```

---

## 6. 단위 테스트

```python
# tests/test_ingestion.py

import pytest
from src.ingestion.layout_parser import LayoutParser, BlockType
from src.ingestion.section_aggregator import SectionAggregator


class TestBlockClassifier:

    def test_check_result_pattern(self):
        parser = LayoutParser()
        blocks = parser._extract_check_results(
            "C.I.P 179.83 < 270.32 O.K 전단력 116.27 < 698.50 O.K",
            page=9
        )
        assert len(blocks) == 2
        assert blocks[0].check_values["calculated"] == 179.83
        assert blocks[0].check_values["allowable"] == 270.32
        assert blocks[0].check_values["result"] == "OK"

    def test_soil_table_detection(self):
        parser = LayoutParser()
        headers = ["지층", "N치", "단위중량\n(kN/m3)", "점착력\n(kN/m2)", "내부마찰각\n( °)"]
        assert parser._is_soil_table(headers) is True

    def test_non_soil_table(self):
        parser = LayoutParser()
        headers = ["구분", "휨응력(MPa)", "전단응력(MPa)", "판단"]
        assert parser._is_soil_table(headers) is False


class TestSectionAggregator:

    def test_extract_section_id(self):
        agg = SectionAggregator()
        text = """
        4-2-17 SEC-1O 검토 요약
        굴착깊이 : 9.48m
        상재하중 : 도로하중(13kN/m²)
        """
        result = agg.extract_section_summary(text, page=9)
        assert result is not None
        assert result["section_id"] == "SEC-1O"
        assert result["excavation_depth"] == 9.48
```

---

## Phase 1 완료 체크리스트

```
[ ] LayoutParser: PDF → 블록 추출 (텍스트/테이블/검토결과)
[ ] BlockType.SOIL_TABLE 정확히 감지
[ ] BlockType.CHECK_RESULT 패턴 추출
[ ] SQLSaver: 블록 → DB 저장
[ ] soil_parameters 테이블 자동 채우기
[ ] SectionAggregator: SEC-1O 등 단면 데이터 구조화
[ ] 검증 CLI 실행: uv run python scripts/verify_parse.py sample.pdf
[ ] pytest 통과
[ ] 수동 SQL 조회: "풍화토-2의 점착력은?" → SELECT c FROM soil_parameters WHERE layer_name='풍화토-2'
```
