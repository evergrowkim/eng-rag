"""파싱 결과 검증 CLI.

사용법:
    uv run python scripts/verify_parse.py path/to/file.pdf
    uv run python scripts/verify_parse.py path/to/file.pdf --save
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

# 프로젝트 루트를 import path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.layout_parser import LayoutParser
from src.ingestion.block_classifier import BlockClassifier
from src.ingestion.section_aggregator import SectionAggregator
from src.ingestion.pipeline import IngestionPipeline


def verify_parse_only(pdf_path: str) -> None:
    """파싱만 수행하여 결과를 출력한다 (DB 저장 없음)."""
    parser = LayoutParser()
    classifier = BlockClassifier()
    aggregator = SectionAggregator()

    doc = parser.parse(pdf_path)
    doc.blocks = classifier.reclassify_blocks(doc.blocks)

    print(f"\n{'='*60}")
    print(f"파일: {pdf_path}")
    print(f"페이지 수: {doc.page_count}")
    print(f"전체 블록 수: {len(doc.blocks)}")
    print(f"메타데이터: {json.dumps(doc.metadata, ensure_ascii=False, indent=2)}")

    # 블록 유형별 통계
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
            print(
                f"  p.{b.page}: {cv.get('calculated')} < "
                f"{cv.get('allowable')} → {cv.get('result')}"
            )

    # 단면 요약 미리보기
    summaries = aggregator.aggregate_from_blocks(doc.blocks)
    if summaries:
        print(f"\n단면 요약 ({len(summaries)}개):")
        for s in summaries[:5]:
            print(
                f"  {s['section_id']} (p.{s['page']}): "
                f"굴착깊이={s.get('excavation_depth')}m"
            )

    print(f"{'='*60}\n")


async def verify_with_save(pdf_path: str) -> None:
    """파싱 + DB 저장까지 수행한다."""
    db_path = "data/db/doaz.db"

    # DB 디렉토리 확인
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    pipeline = IngestionPipeline(db_path=db_path)
    result = await pipeline.ingest(pdf_path)

    print(f"\n{'='*60}")
    print(f"인제스션 결과:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"{'='*60}\n")


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: uv run python scripts/verify_parse.py <pdf_path> [--save]")
        print("  --save: DB에도 저장 (기본: 파싱만)")
        sys.exit(1)

    pdf_path = sys.argv[1]
    save_mode = "--save" in sys.argv

    if not Path(pdf_path).exists():
        print(f"파일 없음: {pdf_path}")
        sys.exit(1)

    if save_mode:
        asyncio.run(verify_with_save(pdf_path))
    else:
        verify_parse_only(pdf_path)


if __name__ == "__main__":
    main()
