"""src/ingestion 모듈 단위 테스트."""

from __future__ import annotations

import pytest

from src.ingestion.layout_parser import BlockType, LayoutParser
from src.ingestion.block_classifier import BlockClassifier
from src.ingestion.section_aggregator import SectionAggregator


# ─── LayoutParser 테스트 ───


class TestCheckResultPattern:
    def test_ok_pattern(self) -> None:
        parser = LayoutParser()
        blocks = parser._extract_check_results(
            "C.I.P 179.83 < 270.32 O.K 전단력 116.27 < 698.50 O.K",
            9,
        )
        assert len(blocks) == 2
        assert blocks[0].check_values["calculated"] == 179.83
        assert blocks[0].check_values["allowable"] == 270.32
        assert blocks[0].check_values["result"] == "OK"

    def test_ng_pattern(self) -> None:
        parser = LayoutParser()
        blocks = parser._extract_check_results(
            "응력 350.5 > 270.0 N.G",
            5,
        )
        assert len(blocks) == 1
        assert blocks[0].check_values["result"] == "NG"
        assert blocks[0].check_values["calculated"] == 350.5

    def test_utilization_ratio(self) -> None:
        parser = LayoutParser()
        blocks = parser._extract_check_results("100.0 < 200.0 OK", 1)
        assert len(blocks) == 1
        assert blocks[0].check_values["utilization"] == 0.5

    def test_no_match(self) -> None:
        parser = LayoutParser()
        blocks = parser._extract_check_results("일반 텍스트입니다.", 1)
        assert len(blocks) == 0


class TestSoilTableDetection:
    def test_soil_table_positive(self) -> None:
        parser = LayoutParser()
        headers = ["지층", "N치", "단위중량\n(kN/m3)", "점착력\n(kN/m2)", "내부마찰각\n( °)"]
        assert parser._is_soil_table(headers) is True

    def test_non_soil_table(self) -> None:
        parser = LayoutParser()
        headers = ["구분", "휨응력(MPa)", "전단응력(MPa)", "판단"]
        assert parser._is_soil_table(headers) is False

    def test_empty_headers(self) -> None:
        parser = LayoutParser()
        assert parser._is_soil_table([]) is False

    def test_partial_match_below_threshold(self) -> None:
        parser = LayoutParser()
        headers = ["지층", "N치", "기타"]
        assert parser._is_soil_table(headers) is False


class TestTableToText:
    def test_basic_conversion(self) -> None:
        parser = LayoutParser()
        headers = ["구분", "값"]
        rows = [{"구분": "A", "값": "10"}, {"구분": "B", "값": "20"}]
        result = parser._table_to_text(headers, rows)
        assert "구분 | 값" in result
        assert "A | 10" in result
        assert "B | 20" in result


class TestMetadataExtraction:
    def test_project_name(self) -> None:
        from src.ingestion.layout_parser import ParsedBlock

        parser = LayoutParser()
        blocks = [
            ParsedBlock(
                block_type=BlockType.TEXT,
                content="서울역 재개발 공사 구조계산서",
                page=1,
            )
        ]
        metadata = parser._extract_metadata(blocks)
        assert metadata["project_name"] is not None
        assert "재개발" in metadata["project_name"]

    def test_standards_extraction(self) -> None:
        from src.ingestion.layout_parser import ParsedBlock

        parser = LayoutParser()
        blocks = [
            ParsedBlock(
                block_type=BlockType.TEXT,
                content="KDS 21 50 00에 따라 설계하며, ACI 318-19 기준 적용",
                page=1,
            )
        ]
        metadata = parser._extract_metadata(blocks)
        assert len(metadata["referenced_standards"]) >= 1


# ─── BlockClassifier 테스트 ───


class TestBlockClassifier:
    def test_classify_soil_table(self) -> None:
        classifier = BlockClassifier()
        headers = ["지층", "N치", "단위중량", "점착력", "내부마찰각"]
        result = classifier.classify_table(headers, [])
        assert result == BlockType.SOIL_TABLE

    def test_classify_generic_table(self) -> None:
        classifier = BlockClassifier()
        headers = ["항목", "수량", "단가"]
        result = classifier.classify_table(headers, [])
        assert result == BlockType.TABLE

    def test_classify_sunex_text(self) -> None:
        classifier = BlockClassifier()
        result = classifier.classify_text("SUNEX 해석결과 Step No.1")
        assert result == BlockType.SUNEX_OUTPUT

    def test_classify_normal_text(self) -> None:
        classifier = BlockClassifier()
        result = classifier.classify_text("일반적인 설계 설명 텍스트")
        assert result == BlockType.TEXT

    def test_extract_check_values(self) -> None:
        classifier = BlockClassifier()
        results = classifier.extract_check_values(
            "휨모멘트 179.83 < 270.32 O.K"
        )
        assert len(results) == 1
        assert results[0]["calculated"] == 179.83
        assert results[0]["operator"] == "<"

    def test_is_section_summary(self) -> None:
        classifier = BlockClassifier()
        assert classifier.is_section_summary("SEC-1O 검토 요약") is True
        assert classifier.is_section_summary("일반 텍스트") is False


# ─── SectionAggregator 테스트 ───


class TestSectionAggregator:
    def test_extract_section_id(self) -> None:
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

    def test_no_section(self) -> None:
        agg = SectionAggregator()
        result = agg.extract_section_summary("일반 텍스트", page=1)
        assert result is None

    def test_extract_moment_pair(self) -> None:
        agg = SectionAggregator()
        calc, allow = agg._extract_check_pair(
            "휨모멘트 179.83 < 270.32", "휨모멘트"
        )
        assert calc == 179.83
        assert allow == 270.32

    def test_extract_safety_factor(self) -> None:
        agg = SectionAggregator()
        sf = agg._extract_safety_factor("근입장 안전율 1.520")
        assert sf == 1.52

    def test_extract_displacement(self) -> None:
        agg = SectionAggregator()
        calc, allow = agg._extract_displacement(
            "두부변위 15.3mm (허용 25.0mm)", "두부변위"
        )
        assert calc == 15.3
        assert allow == 25.0
