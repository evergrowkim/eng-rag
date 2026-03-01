# 07. 성능 평가 (Evaluation)

## 1. 평가 질문셋

```python
# tests/eval_questions.py
"""
엔지니어링 RAG 평가용 질문셋.
업로드된 흙막이 설계보고서(전주 기자촌) 기준.
"""

EVAL_QUESTIONS = [

    # ── 수치형 (SQL 우선) ─────────────────────────────────────────────
    {
        "id": "N-01",
        "query": "풍화토-2의 점착력은?",
        "expected": "10.0 kN/m²",
        "type": "numerical",
        "tool": "sql",
        "source_table": "soil_parameters"
    },
    {
        "id": "N-02",
        "query": "풍화암의 수평지반반력계수는?",
        "expected": "50,000 kN/m³",
        "type": "numerical",
        "tool": "sql",
        "source_table": "soil_parameters"
    },
    {
        "id": "N-03",
        "query": "SEC-1O 단면의 CIP 발생 휨모멘트는?",
        "expected": "179.83 kN·m",
        "type": "numerical",
        "tool": "sql",
        "source_table": "section_checks"
    },
    {
        "id": "N-04",
        "query": "SEC-1O의 두부변위와 허용치는?",
        "expected": "4.90mm / 30.00mm",
        "type": "numerical",
        "tool": "sql",
        "source_table": "section_checks"
    },
    {
        "id": "N-05",
        "query": "굴착깊이가 가장 깊은 단면은?",
        "expected": "SEC-1O 또는 SEC-1P(R) — 9.48m",
        "type": "numerical",
        "tool": "sql",
        "source_table": "section_checks"
    },

    # ── 적합성 판정 (SQL + Vector) ────────────────────────────────────
    {
        "id": "C-01",
        "query": "SEC-1O의 근입깊이 안전율이 허용 기준을 만족하는가?",
        "expected": "만족 (2.255 > 1.20 O.K)",
        "type": "compliance",
        "tool": "sql+vector"
    },
    {
        "id": "C-02",
        "query": "SEC-2A 단면의 모든 부재 검토가 허용응력 이내인가?",
        "expected": "O.K (CIP 77.24 < 270.32, SF 2.761 > 1.20)",
        "type": "compliance",
        "tool": "sql+vector"
    },

    # ── 개념형 (Vector 우선) ─────────────────────────────────────────
    {
        "id": "V-01",
        "query": "일시앙카와 영구앙카의 안전율 차이는?",
        "expected": "일시앙카 1.5 / 영구앙카 상시 2.5, 지진시 1.5~2.0",
        "type": "conceptual",
        "tool": "vector"
    },
    {
        "id": "V-02",
        "query": "이 보고서에서 사용된 강재 종류는?",
        "expected": "SS275, SM275, SHP275(W), SM355, SHP355W",
        "type": "conceptual",
        "tool": "vector"
    },
    {
        "id": "V-03",
        "query": "지반정수 산정 방법의 흐름은?",
        "expected": "직접적방법(현장시험→실내시험) + 간접적방법(N값+경험식) → 최종 결정",
        "type": "conceptual",
        "tool": "vector"
    },

    # ── 비교형 (SQL 집계) ────────────────────────────────────────────
    {
        "id": "CP-01",
        "query": "SEC-1O와 SEC-2A의 굴착깊이와 상재하중 비교",
        "expected": "SEC-1O: 9.48m, 13kN/m² / SEC-2A: 9.48m, 74.23+13kN/m²",
        "type": "comparative",
        "tool": "sql"
    },
    {
        "id": "CP-02",
        "query": "모든 단면 중 근입깊이 안전율이 가장 높은 것은?",
        "expected": "SEC-1P(L): 5.178",
        "type": "comparative",
        "tool": "sql"
    },

    # ── 멀티홉 (SQL + Vector + PageIndex) ─────────────────────────────
    {
        "id": "MH-01",
        "query": "SEC-1O 단면에서 사용된 앵커 정착장 산정 근거는?",
        "expected": "4단 앵커 / 자유장 7.5m, 정착장 6.0m / 설계축력 320kN",
        "type": "multi_hop",
        "tool": "all"
    },
]
```

---

## 2. 자동 평가 스크립트

```python
# tests/run_eval.py

import asyncio
import json
from datetime import datetime
from loguru import logger

from src.retrieval.search_engine import SearchEngine
from .eval_questions import EVAL_QUESTIONS


async def run_evaluation(doc_ids: list[str] | None = None):
    engine = SearchEngine("data/db/doaz.db")
    results = []

    print(f"\n{'='*60}")
    print(f"Doaz Engineering RAG 평가 시작")
    print(f"질문 수: {len(EVAL_QUESTIONS)}")
    print(f"{'='*60}\n")

    for i, q in enumerate(EVAL_QUESTIONS, 1):
        print(f"[{i:02d}/{len(EVAL_QUESTIONS)}] {q['id']}: {q['query']}")

        try:
            result = await engine.search(q["query"], doc_ids)

            # 간단한 키워드 매칭으로 정답 포함 여부 확인
            expected_keywords = [w for w in q["expected"].split() if len(w) > 1]
            answer_lower = result.answer.lower()
            matches = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
            accuracy = matches / len(expected_keywords) if expected_keywords else 0

            record = {
                "id": q["id"],
                "query": q["query"],
                "expected": q["expected"],
                "answer": result.answer,
                "query_type_expected": q["type"],
                "query_type_actual": result.query_type,
                "type_match": q["type"] == result.query_type,
                "keyword_match_rate": round(accuracy, 2),
                "sql_used": result.sql_result is not None,
                "vector_count": len(result.vector_results),
            }

            status = "✅" if accuracy > 0.5 else "❌"
            print(f"  {status} 유형: {result.query_type} | 키워드 매칭: {accuracy:.0%}")
            print(f"  답변 미리보기: {result.answer[:80]}...")
            print()

        except Exception as e:
            logger.error(f"평가 실패: {e}")
            record = {"id": q["id"], "error": str(e)}

        results.append(record)

    # 결과 저장
    output_path = f"data/eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 요약
    total = len([r for r in results if "error" not in r])
    passed = len([r for r in results if r.get("keyword_match_rate", 0) > 0.5])
    type_correct = len([r for r in results if r.get("type_match")])

    print(f"\n{'='*60}")
    print(f"평가 결과 요약")
    print(f"  전체: {total}개")
    print(f"  답변 정확도 (키워드 매칭 > 50%): {passed}/{total} = {passed/total:.0%}")
    print(f"  쿼리 유형 분류 정확도: {type_correct}/{total} = {type_correct/total:.0%}")
    print(f"  결과 저장: {output_path}")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    asyncio.run(run_evaluation())
```

---

## 3. 평가 지표 기준

| 지표 | 목표 | 측정 방법 |
|------|------|-----------|
| 수치형 정확도 | 95% | 정확한 숫자 포함 여부 |
| 쿼리 유형 분류 정확도 | 90% | SQL/Vector 올바른 선택 |
| 개념형 관련성 | 85% | 핵심 키워드 포함률 |
| 평균 응답 시간 | < 10초 | wall clock time |
| 출처 명시율 | 100% | 페이지/섹션 포함 여부 |

---

## 4. 개별 모듈 테스트

```bash
# 파싱 검증
uv run python scripts/verify_parse.py data/uploads/sample.pdf

# 전체 평가 실행
uv run python tests/run_eval.py

# 단위 테스트
uv run pytest tests/test_ingestion.py -v
uv run pytest tests/ -v --tb=short

# 특정 질문만 테스트
uv run python -c "
import asyncio
from src.retrieval.search_engine import SearchEngine

async def test():
    engine = SearchEngine('data/db/doaz.db')
    result = await engine.search('풍화토-2의 점착력은?')
    print('답변:', result.answer)
    print('SQL:', result.sql_result)

asyncio.run(test())
"
```

---

## 5. 성능 개선 가이드

### 정확도가 낮을 때 체크리스트

```
수치형 답변이 틀릴 때:
  [ ] SQL 스키마가 올바르게 채워졌는지 확인
      → uv run python -c "..."로 직접 SQL 조회
  [ ] 테이블 파싱 정확도 확인
      → scripts/verify_parse.py 실행
  [ ] NL→SQL 프롬프트 개선 (스키마 설명 보강)

Vector 검색이 관련 없는 결과를 반환할 때:
  [ ] 청크 텍스트 품질 확인 (임베딩할 텍스트가 자연어인지)
  [ ] top_k 값 조정 (기본 5 → 10)
  [ ] 메타데이터 필터 추가 (block_type 필터)
  [ ] 재인덱싱 (임베딩 전략 변경 후)

쿼리 분류가 틀릴 때:
  [ ] QueryClassifier 신호어 추가
  [ ] 분류 로그 확인 (INFO 레벨)
```
