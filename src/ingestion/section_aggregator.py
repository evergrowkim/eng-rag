"""단면 검토 요약 집계기.

SEC-1O, SEC-2A 등 단면 검토 요약 섹션을 파싱하여 구조화된 데이터로 변환한다.

패턴 예시:
    "4-2-17 SEC-1O 검토 요약"
    → section_id = "SEC-1O"
    → excavation_depth = 9.48
    → anchor_count = 4
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from .layout_parser import BlockType, ParsedBlock


class SectionAggregator:
    """단면 검토 요약을 파싱하여 구조화한다."""

    SECTION_RE = re.compile(r"SEC-(\w+)")
    DEPTH_RE = re.compile(r"굴착깊이\s*:\s*(\d+\.?\d*)\s*m")
    SURCHARGE_RE = re.compile(r"상재하중\s*:\s*[\w가-힣()]*\s*\(?(\d+\.?\d*)\s*kN/m")

    def extract_section_summary(self, text: str, page: int) -> dict[str, Any] | None:
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

    def aggregate_from_blocks(self, blocks: list[ParsedBlock]) -> list[dict[str, Any]]:
        """블록 리스트에서 모든 단면 요약을 추출한다."""
        summaries: list[dict[str, Any]] = []

        for block in blocks:
            if block.block_type != BlockType.TEXT:
                continue
            summary = self.extract_section_summary(block.content, block.page)
            if summary:
                logger.debug(f"단면 요약 발견: {summary['section_id']} (p.{block.page})")
                summaries.append(summary)

        logger.info(f"단면 요약 {len(summaries)}개 추출 완료")
        return summaries

    def _extract_check_pair(
        self, text: str, keyword: str
    ) -> tuple[float | None, float | None]:
        """'keyword ... 179.83 < 270.32' 패턴에서 계산값/허용값 추출."""
        pattern = re.compile(rf"{keyword}.*?(\d+\.?\d+)\s*<\s*(\d+\.?\d+)")
        m = pattern.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))
        return None, None

    def _extract_safety_factor(self, text: str) -> float | None:
        """안전율 수치 추출."""
        m = re.search(r"안전율\s+(\d+\.?\d+)", text)
        return float(m.group(1)) if m else None

    def _extract_displacement(
        self, text: str, keyword: str
    ) -> tuple[float | None, float | None]:
        """변위 수치 추출 (계산값 mm, 허용값 mm)."""
        m = re.search(rf"{keyword}.*?(\d+\.?\d+)\s*mm.*?(\d+\.?\d+)\s*mm", text)
        if m:
            return float(m.group(1)), float(m.group(2))
        return None, None
