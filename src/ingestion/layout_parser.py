"""PDF 레이아웃 인식 파서.

엔지니어링 설계보고서 PDF를 블록 단위로 파싱한다.
처리 순서:
  1. pdfplumber로 테이블 먼저 감지 (테이블은 좌표로 예약)
  2. 나머지 영역에서 텍스트 블록 추출
  3. 각 블록 유형 분류
  4. 특수 패턴 (검토결과, 지반정수, SUNEX) 처리
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pdfplumber
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
    """엔지니어링 PDF 레이아웃 인식 파서."""

    # 패턴 정의
    CHECK_RESULT_RE = re.compile(
        r"(\d+\.?\d*)\s*[<>]\s*(\d+\.?\d*)\s*(O\.?K|N\.?G)", re.IGNORECASE
    )
    SOIL_HEADERS: set[str] = {"N치", "단위중량", "점착력", "내부마찰각", "수평지반반력계수"}
    SUNEX_MARKERS: set[str] = {"SUNEX", "Step No.", "EXCA TO", "kN/ea"}

    def parse(self, pdf_path: str) -> ParsedDocument:
        """PDF 파일을 파싱하여 ParsedDocument를 반환한다."""
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

    def _extract_tables(self, page: pdfplumber.page.Page, page_num: int) -> list[ParsedBlock]:
        """테이블을 구조화 형태로 추출."""
        blocks: list[ParsedBlock] = []
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
            rows: list[dict[str, str]] = []
            for row in table[1:]:
                row_dict: dict[str, str] = {}
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
                raw_rows=table,
            ))

        return blocks

    def _extract_text_blocks(
        self,
        page: pdfplumber.page.Page,
        page_num: int,
        table_blocks: list[ParsedBlock],
    ) -> list[ParsedBlock]:
        """텍스트 블록 추출 및 분류."""
        blocks: list[ParsedBlock] = []
        text = page.extract_text() or ""

        if not text.strip():
            return blocks

        # SUNEX 출력 감지
        if any(marker in text for marker in self.SUNEX_MARKERS):
            return [ParsedBlock(
                block_type=BlockType.SUNEX_OUTPUT,
                content=text,
                page=page_num,
            )]

        # 검토결과 패턴 감지
        check_blocks = self._extract_check_results(text, page_num)
        blocks.extend(check_blocks)

        # 일반 텍스트 (섹션 단위로 분리)
        text_block = ParsedBlock(
            block_type=BlockType.TEXT,
            content=text,
            page=page_num,
        )
        blocks.append(text_block)

        return blocks

    def _extract_check_results(self, text: str, page_num: int) -> list[ParsedBlock]:
        """'179.83 < 270.32 O.K' 형태의 검토결과 추출."""
        blocks: list[ParsedBlock] = []
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
                    "utilization": round(calc_val / allow_val, 3) if allow_val else None,
                },
            ))

        return blocks

    def _is_soil_table(self, headers: list[str]) -> bool:
        """지반정수 테이블 여부 판별.

        헤더에 줄바꿈·단위 표기가 포함될 수 있으므로 부분 문자열 매칭을 사용한다.
        예: "단위중량\\n(kN/m3)" → "단위중량" 매칭
        """
        match_count = 0
        for header in headers:
            if not header:
                continue
            for soil_key in self.SOIL_HEADERS:
                if soil_key in header:
                    match_count += 1
                    break
        return match_count >= 3

    def _table_to_text(self, headers: list[str], rows: list[dict[str, str]]) -> str:
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
        standards = re.findall(
            r"KDS[\s\d]+|KCS[\s\d]+|ACI[\s\d-]+|ASME[\s\w-]+", full_text
        )
        metadata["referenced_standards"] = list(set(standards))

        return metadata
