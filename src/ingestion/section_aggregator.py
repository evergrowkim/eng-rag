"""단면 검토 요약 집계기.

SEC-1O, SEC-2A 등 단면 검토 요약 섹션을 파싱하여 구조화된 데이터로 변환한다.

실제 PDF 형식 예시 (이미지 확인 기준):
    "4-2-9 SEC-1H(R) 검토 요약"

    흙막이벽체 및 지보공법:
      o 굴착깊이 : 12.69m
      o 상재하중 : 도로하중(13kN/m²)

    부재 검토 결과 요약:
      o C.I.P
        C.I.P   208.36 < 270.32  127.96 < 698.50  O.K

      o 근입깊이
        근입깊이(m)  안전율  허용안전율  판 단
        3.0          1.806   1.20        O.K

      o 흙막이 벽체변위 및 두부변위
        두부변위(초기)    안전한계치    판 단    벽체변위(최대)   벽체허용변위  판 단
        8.00mm           30.00mm       O.K      12.60mm         25.40mm       O.K
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from .layout_parser import BlockType, ParsedBlock


class SectionAggregator:
    """단면 검토 요약을 파싱하여 구조화한다."""

    SECTION_RE = re.compile(r"SEC-(\w+(?:\([^)]*\))?)")

    # 굴착깊이 : 12.69m
    DEPTH_RE = re.compile(r"굴착깊이\s*[:\s]\s*(\d+\.?\d*)\s*m")

    # 상재하중 : 도로하중(13kN/m²)
    SURCHARGE_RE = re.compile(r"상재하중\s*[:\s].*?(\d+\.?\d*)\s*kN")

    # 근입깊이 테이블: "3.0  1.806  1.20"
    EMBEDMENT_TABLE_RE = re.compile(
        r"근입(?:깊이|장).*?\n\s*(\d+\.?\d+)\s+(\d+\.?\d+)\s+(\d+\.?\d+)"
    )

    # 두부변위 8.00mm  30.00mm
    HEAD_DISP_RE = re.compile(
        r"두부변위.*?(\d+\.?\d+)\s*mm\s+(\d+\.?\d+)\s*mm"
    )

    # 벽체변위(최대) 인라인
    WALL_DISP_INLINE_RE = re.compile(
        r"벽체(?:변위|허용변위)\s*\(?\s*최대\s*\)?\s*"
        r"(\d+\.?\d+)\s*mm.*?"
        r"벽체허용변위\s*(\d+\.?\d+)\s*mm"
    )

    # 단순 안전율
    SF_SIMPLE_RE = re.compile(r"안전율\s+(\d+\.?\d+)")

    # 전체 OK 판정
    OVERALL_OK_RE = re.compile(r"안정성\s*확보|전\s*구간.*?O\.?K|모두\s*(?:만족|이내)")

    def extract_section_summary(self, text: str, page: int) -> dict[str, Any] | None:
        """텍스트에서 단면 검토 요약 추출."""
        sec_match = self.SECTION_RE.search(text)
        if not sec_match:
            return None

        section_id = f"SEC-{sec_match.group(1)}"

        depth_match = self.DEPTH_RE.search(text)
        surcharge_match = self.SURCHARGE_RE.search(text)

        # 부재 검토 결과
        moment_calc, moment_allow = self._extract_check_pair(text, "휨모멘트")
        if moment_calc is None:
            moment_calc, moment_allow = self._extract_check_pair(text, r"C\.?I\.?P")

        # 근입장 안전율
        embed_sf, embed_sf_allow, embed_depth = self._extract_embedment(text)

        # 변위
        head_disp, head_disp_allow = self._extract_head_displacement(text)
        max_disp, max_disp_allow = self._extract_wall_displacement(text)

        # 전체 판정
        overall = self._extract_overall_result(text)

        # 앵커/지보재
        anchor_info = self._extract_anchor_info(text)

        return {
            "section_id": section_id,
            "page": page,
            "excavation_depth": float(depth_match.group(1)) if depth_match else None,
            "surcharge_load": float(surcharge_match.group(1)) if surcharge_match else None,
            "moment_calc": moment_calc,
            "moment_allow": moment_allow,
            "embedment_depth": embed_depth,
            "embedment_SF": embed_sf,
            "embedment_SF_allow": embed_sf_allow,
            "head_disp_calc": head_disp,
            "head_disp_allow": head_disp_allow,
            "max_disp_calc": max_disp,
            "max_disp_allow": max_disp_allow,
            "overall_result": overall,
            "anchor_info": anchor_info,
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

    def _extract_embedment(
        self, text: str
    ) -> tuple[float | None, float | None, float | None]:
        """근입장 안전율 추출. (SF, SF_allow, depth) 반환."""
        # 패턴 1: 테이블형 "근입깊이...\n3.0  1.806  1.20"
        m = self.EMBEDMENT_TABLE_RE.search(text)
        if m:
            return float(m.group(2)), float(m.group(3)), float(m.group(1))

        # 패턴 2: 단순 안전율
        m = self.SF_SIMPLE_RE.search(text)
        if m:
            return float(m.group(1)), None, None

        return None, None, None

    def _extract_head_displacement(
        self, text: str
    ) -> tuple[float | None, float | None]:
        """두부변위 추출 (계산값mm, 허용값mm)."""
        m = self.HEAD_DISP_RE.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))
        return None, None

    def _extract_wall_displacement(
        self, text: str
    ) -> tuple[float | None, float | None]:
        """벽체변위(최대) 추출."""
        # 패턴 1: "벽체변위(최대) 12.60mm 벽체허용변위 25.40mm"
        m = self.WALL_DISP_INLINE_RE.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))

        # 패턴 2: 두부변위 뒤 "O.K 12.60mm 25.40mm"
        m = re.search(
            r"두부변위.*?O\.?K\s+(\d+\.?\d+)\s*mm\s+(\d+\.?\d+)\s*mm",
            text,
        )
        if m:
            return float(m.group(1)), float(m.group(2))

        return None, None

    def _extract_overall_result(self, text: str) -> str | None:
        """전체 판정 결과 추출."""
        if self.OVERALL_OK_RE.search(text):
            return "OK"

        ok_count = len(re.findall(r"O\.?K", text, re.IGNORECASE))
        ng_count = len(re.findall(r"N\.?G", text, re.IGNORECASE))

        if ng_count > 0:
            return "NG"
        if ok_count >= 3:
            return "OK"

        return None

    def _extract_anchor_info(self, text: str) -> dict[str, Any] | None:
        """앵커/지보재 정보 추출."""
        m = re.search(
            r"EA\s*(\d+)단\s+(\d+\.?\d+)\s+(\d+\.?\d+)\s+(\d+\.?\d*)\s+(\d+\.?\d*)",
            text,
        )
        if m:
            return {
                "stage": int(m.group(1)),
                "free_length": float(m.group(2)),
                "anchor_length": float(m.group(3)),
                "design_force": float(m.group(4)),
                "tensile_force": float(m.group(5)),
            }
        return None
