"""블록 유형 분류기.

LayoutParser가 추출한 원시 블록의 세부 유형을 재분류하고,
엔지니어링 도메인별 패턴을 적용하여 블록 메타데이터를 보강한다.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from .layout_parser import BlockType, ParsedBlock


class BlockClassifier:
    """파싱된 블록의 유형을 세분화하여 재분류한다."""

    # ── 지반정수 테이블 ──
    SOIL_HEADERS: set[str] = {"N치", "단위중량", "점착력", "내부마찰각", "수평지반반력계수"}
    SOIL_HEADER_THRESHOLD = 3

    # ── SUNEX 해석 출력 ──
    SUNEX_MARKERS: set[str] = {"SUNEX", "Step No.", "EXCA TO", "kN/ea"}

    # ── 검토결과 (OK/NG) ──
    CHECK_RESULT_RE = re.compile(
        r"(\d+\.?\d*)\s*([<>])\s*(\d+\.?\d*)\s*(O\.?K|N\.?G)", re.IGNORECASE
    )

    # ── 앵커 설계 테이블 ──
    ANCHOR_HEADERS: set[str] = {"자유장", "정착장", "설계력", "인장력", "앵커"}

    # ── 재료 허용응력 테이블 ──
    MATERIAL_HEADERS: set[str] = {"허용응력", "항복강도", "인장강도", "강종"}

    # ── 단면 검토 요약 ──
    SECTION_SUMMARY_RE = re.compile(r"SEC-\w+.*검토\s*요약", re.IGNORECASE)

    def classify_table(self, headers: list[str], raw_rows: list[list[str]]) -> BlockType:
        """테이블 헤더를 분석하여 세부 BlockType을 반환한다."""
        header_set = {h.strip() for h in headers if h and h.strip()}

        if self._matches(header_set, self.SOIL_HEADERS, self.SOIL_HEADER_THRESHOLD):
            return BlockType.SOIL_TABLE

        # 기본 테이블
        return BlockType.TABLE

    def classify_text(self, text: str) -> BlockType:
        """텍스트 내용을 분석하여 BlockType을 반환한다."""
        if any(marker in text for marker in self.SUNEX_MARKERS):
            return BlockType.SUNEX_OUTPUT

        return BlockType.TEXT

    def extract_check_values(self, text: str) -> list[dict[str, Any]]:
        """텍스트에서 검토결과 수치를 추출한다.

        Returns:
            [{"calculated": float, "allowable": float, "operator": str,
              "result": str, "utilization": float | None}, ...]
        """
        results: list[dict[str, Any]] = []
        for match in self.CHECK_RESULT_RE.finditer(text):
            calc_val = float(match.group(1))
            operator = match.group(2)
            allow_val = float(match.group(3))
            result = match.group(4).replace(".", "")  # OK or NG

            results.append({
                "calculated": calc_val,
                "allowable": allow_val,
                "operator": operator,
                "result": result,
                "utilization": round(calc_val / allow_val, 3) if allow_val else None,
            })

        return results

    def is_section_summary(self, text: str) -> bool:
        """단면 검토 요약 페이지인지 판별한다."""
        return bool(self.SECTION_SUMMARY_RE.search(text))

    def reclassify_blocks(self, blocks: list[ParsedBlock]) -> list[ParsedBlock]:
        """이미 파싱된 블록 리스트를 재분류한다.

        LayoutParser가 1차 분류한 결과를 더 세밀하게 보정한다.
        """
        reclassified: list[ParsedBlock] = []

        for block in blocks:
            if block.block_type == BlockType.TABLE and block.raw_rows:
                headers = [str(cell or "").strip() for cell in block.raw_rows[0]]
                new_type = self.classify_table(headers, block.raw_rows)
                if new_type != block.block_type:
                    logger.debug(
                        f"블록 재분류: p.{block.page} {block.block_type} → {new_type}"
                    )
                    block.block_type = new_type

            elif block.block_type == BlockType.TEXT:
                new_type = self.classify_text(block.content)
                if new_type != block.block_type:
                    logger.debug(
                        f"블록 재분류: p.{block.page} {block.block_type} → {new_type}"
                    )
                    block.block_type = new_type

            reclassified.append(block)

        return reclassified

    @staticmethod
    def _matches(header_set: set[str], target_headers: set[str], threshold: int) -> bool:
        """헤더 집합이 대상 헤더와 threshold 이상 겹치는지 검사한다."""
        return len(header_set & target_headers) >= threshold
