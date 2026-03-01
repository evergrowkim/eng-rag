# 05. 검색 엔진 (Query Routing + Retrieval)

## Phase 3 구현 목표
자연어 질문 → 자동 분류 → SQL/Vector 병렬 검색 → 통합 답변

---

## 1. 쿼리 분류기

```python
# src/retrieval/query_classifier.py

import re
from dataclasses import dataclass
from enum import Enum
from loguru import logger


class QueryType(str, Enum):
    NUMERICAL = "numerical"       # "안전율이 얼마인가" → SQL 우선
    CONCEPTUAL = "conceptual"     # "앵커 설계 방법은" → Vector 우선
    COMPLIANCE = "compliance"     # "기준 만족하는가" → SQL + Vector
    COMPARATIVE = "comparative"   # "A와 B 비교" → SQL 집계
    MULTI_HOP = "multi_hop"       # 복합 추론 → 모든 도구


@dataclass
class RoutingPlan:
    query_type: QueryType
    use_sql: bool
    use_vector: bool
    use_pageindex: bool
    parallel: bool
    priority: str    # "sql_first" | "vector_first" | "equal"


class QueryClassifier:

    # 수치형 신호어
    NUMERICAL_SIGNALS = [
        r"\d+\s*(m|mm|kN|MPa|kPa|°)",  # 단위 포함 숫자
        "몇", "얼마", "값", "수치", "결과", "크기",
        "최대", "최소", "평균", "합계", "총", "전체",
        "안전율", "변위", "깊이", "하중", "응력", "모멘트"
    ]

    # 적합성 신호어
    COMPLIANCE_SIGNALS = [
        "만족", "충족", "적합", "준수", "부합",
        "위반", "초과", "미달", "이하", "이상",
        "O.K", "N.G", "검토", "판정"
    ]

    # 비교 신호어
    COMPARATIVE_SIGNALS = [
        "비교", "차이", "vs", "대비", "versus",
        "어느", "가장", "제일", "더", "덜"
    ]

    # 멀티홉 신호어
    MULTI_HOP_SIGNALS = [
        "근거", "이유", "왜", "어떻게 계산",
        "산정 방법", "적용 기준"
    ]

    def classify(self, query: str) -> RoutingPlan:
        query_lower = query.lower()

        # 우선순위 순서로 분류
        if self._matches(query, self.MULTI_HOP_SIGNALS):
            return RoutingPlan(
                query_type=QueryType.MULTI_HOP,
                use_sql=True, use_vector=True, use_pageindex=True,
                parallel=True, priority="equal"
            )

        if self._matches(query, self.COMPARATIVE_SIGNALS):
            return RoutingPlan(
                query_type=QueryType.COMPARATIVE,
                use_sql=True, use_vector=True, use_pageindex=False,
                parallel=True, priority="sql_first"
            )

        if self._matches(query, self.COMPLIANCE_SIGNALS):
            return RoutingPlan(
                query_type=QueryType.COMPLIANCE,
                use_sql=True, use_vector=True, use_pageindex=False,
                parallel=True, priority="sql_first"
            )

        if self._matches(query, self.NUMERICAL_SIGNALS):
            return RoutingPlan(
                query_type=QueryType.NUMERICAL,
                use_sql=True, use_vector=False, use_pageindex=False,
                parallel=False, priority="sql_first"
            )

        # 기본: 개념형
        return RoutingPlan(
            query_type=QueryType.CONCEPTUAL,
            use_sql=False, use_vector=True, use_pageindex=True,
            parallel=False, priority="vector_first"
        )

    def _matches(self, text: str, signals: list[str]) -> bool:
        for signal in signals:
            if signal.startswith(r"\\") or "(" in signal:
                if re.search(signal, text):
                    return True
            elif signal in text:
                return True
        return False
```

---

## 2. SQL 검색 도구

```python
# src/retrieval/sql_tool.py

import json
import re
from loguru import logger
import aiosqlite
from anthropic import AsyncAnthropic


# 시스템이 알고있는 스키마 (LLM에게 제공)
DB_SCHEMA = """
SQLite 데이터베이스 스키마:

documents(id, filename, doc_type, project_name, uploaded_at, page_count)

soil_parameters(id, doc_id, borehole_id, layer_name, N_value,
                unit_weight, cohesion, friction_angle, kh, page_number)
  -- 지반 정수: 단위중량(kN/m³), 점착력(kN/m²), 내부마찰각(°), 수평반력계수(kN/m³)

section_checks(id, doc_id, section_id, wall_type, support_type,
               excavation_depth, surcharge_load,
               moment_calc, moment_allow, shear_calc, shear_allow,
               rebar_required, rebar_provided,
               embedment_depth, embedment_SF, embedment_SF_allow,
               head_disp_calc, head_disp_allow,
               max_disp_calc, max_disp_allow,
               overall_result, page_number)
  -- 단면 검토: section_id 예시 'SEC-1O', 'SEC-2A'
  -- overall_result: 'OK' 또는 'NG'

anchor_design(id, doc_id, section_id, stage, free_length, anchor_length,
              design_force, tensile_force, usage_type)
  -- usage_type: 'TEMPORARY' 또는 'PERMANENT'

material_allowables(id, doc_id, material_grade, stress_type,
                    allowable_mpa, condition, page_number)
"""

SQL_SYSTEM_PROMPT = f"""
당신은 엔지니어링 데이터베이스 SQL 전문가입니다.

{DB_SCHEMA}

규칙:
1. SELECT 쿼리만 생성 (INSERT/UPDATE/DELETE 절대 금지)
2. 없는 테이블/컬럼 사용 금지
3. 불가능한 쿼리면 CANNOT_ANSWER 반환
4. SQL 코드만 반환 (설명 없이, 마크다운 없이)
5. 한국어 값 처리 예시: WHERE layer_name = '풍화토-2'
"""


class SQLTool:

    def __init__(self, db_path: str, anthropic_client: AsyncAnthropic):
        self.db_path = db_path
        self.anthropic = anthropic_client

    async def query(self, question: str, doc_ids: list[str] | None = None) -> dict:
        """
        자연어 질문 → SQL 생성 → 실행 → 결과 반환.
        
        Returns:
            {
                "success": bool,
                "sql": str,
                "rows": list,
                "columns": list,
                "error": str | None
            }
        """
        # 1. NL → SQL
        sql = await self._generate_sql(question, doc_ids)
        logger.debug(f"생성된 SQL: {sql}")

        if "CANNOT_ANSWER" in sql:
            return {"success": False, "sql": sql, "rows": [], "error": "SQL 생성 불가"}

        # 안전 검증
        if not self._is_safe_sql(sql):
            return {"success": False, "sql": sql, "rows": [], "error": "위험한 SQL"}

        # 2. 실행
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(sql) as cursor:
                    rows = await cursor.fetchall()
                    columns = [d[0] for d in cursor.description] if cursor.description else []
                    row_dicts = [dict(row) for row in rows]

            return {
                "success": True,
                "sql": sql,
                "rows": row_dicts,
                "columns": columns,
                "row_count": len(row_dicts)
            }
        except Exception as e:
            logger.error(f"SQL 실행 오류: {e}")
            return {"success": False, "sql": sql, "rows": [], "error": str(e)}

    async def _generate_sql(self, question: str, doc_ids: list[str] | None) -> str:
        """Claude API로 SQL 생성."""
        doc_filter = ""
        if doc_ids:
            ids = ", ".join(f"'{d}'" for d in doc_ids)
            doc_filter = f"\n추가 조건: doc_id IN ({ids}) 필터 적용"

        response = await self.anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=SQL_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"{question}{doc_filter}"
            }]
        )

        sql = response.content[0].text.strip()
        # 마크다운 제거
        sql = re.sub(r"```sql\s*", "", sql)
        sql = re.sub(r"```\s*", "", sql)
        return sql.strip()

    def _is_safe_sql(self, sql: str) -> bool:
        """위험한 SQL 차단."""
        sql_upper = sql.upper()
        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE"]
        return not any(keyword in sql_upper for keyword in dangerous)
```

---

## 3. 벡터 검색 도구

```python
# src/retrieval/vector_tool.py

from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from loguru import logger


class VectorTool:

    def __init__(self, qdrant_client: QdrantClient, openai_client: AsyncOpenAI):
        self.qdrant = qdrant_client
        self.openai = openai_client
        self.collection = "doaz_eng_rag"

    async def search(
        self,
        query: str,
        top_k: int = 5,
        doc_ids: list[str] | None = None,
        block_types: list[str] | None = None
    ) -> list[dict]:
        """
        의미 기반 벡터 검색.
        
        Returns: [{"content", "page", "doc_id", "block_type", "score"}]
        """
        # 1. 쿼리 임베딩
        response = await self.openai.embeddings.create(
            model="text-embedding-3-large",
            input=query
        )
        query_vector = response.data[0].embedding

        # 2. 필터 구성
        filters = []
        if doc_ids:
            filters.append(FieldCondition(
                key="doc_id",
                match=MatchValue(any=doc_ids)
            ))
        if block_types:
            filters.append(FieldCondition(
                key="block_type",
                match=MatchValue(any=block_types)
            ))

        qdrant_filter = Filter(must=filters) if filters else None

        # 3. 검색
        results = self.qdrant.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True
        )

        return [
            {
                "content": r.payload.get("content", ""),
                "page": r.payload.get("page_number"),
                "doc_id": r.payload.get("doc_id"),
                "block_type": r.payload.get("block_type"),
                "score": round(r.score, 4),
                "filename": r.payload.get("filename"),
            }
            for r in results
        ]
```

---

## 4. 메인 검색 엔진 (라우터)

```python
# src/retrieval/search_engine.py

import asyncio
from dataclasses import dataclass
from loguru import logger
from anthropic import AsyncAnthropic

from .query_classifier import QueryClassifier, QueryType
from .sql_tool import SQLTool
from .vector_tool import VectorTool


@dataclass
class SearchResult:
    query: str
    query_type: str
    sql_result: dict | None
    vector_results: list[dict]
    answer: str
    sources: list[dict]


ANSWER_PROMPT = """
당신은 엔지니어링 설계 전문 AI입니다.

## 데이터베이스 조회 결과
{sql_section}

## 문서 검색 결과
{vector_section}

## 질문
{query}

## 답변 규칙
1. 수치는 반드시 단위 포함 (예: 179.83 kN·m, 2.255, 13 kN/m²)
2. 출처를 반드시 명시: [문서명 p.페이지]
3. 기준값과 계산값 구분
4. 판정이 필요하면 명확하게: O.K / N.G
5. 확실하지 않으면 "확인 필요" — 절대 추측 금지
6. 답변은 간결하게 (3~5문장)
"""


class SearchEngine:

    def __init__(
        self,
        db_path: str,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333
    ):
        from qdrant_client import QdrantClient
        from openai import AsyncOpenAI

        self.classifier = QueryClassifier()
        self.anthropic = AsyncAnthropic()

        qdrant = QdrantClient(host=qdrant_host, port=qdrant_port)
        openai = AsyncOpenAI()

        self.sql_tool = SQLTool(db_path, self.anthropic)
        self.vector_tool = VectorTool(qdrant, openai)

    async def search(
        self,
        query: str,
        doc_ids: list[str] | None = None
    ) -> SearchResult:
        """
        질문 분류 → 도구 선택 → 검색 → 답변 생성.
        """
        logger.info(f"검색 시작: '{query}'")

        # 1. 분류
        plan = self.classifier.classify(query)
        logger.info(f"쿼리 유형: {plan.query_type}, SQL={plan.use_sql}, Vector={plan.use_vector}")

        # 2. 병렬/직렬 검색
        sql_result = None
        vector_results = []

        if plan.parallel:
            tasks = []
            if plan.use_sql:
                tasks.append(self.sql_tool.query(query, doc_ids))
            if plan.use_vector:
                tasks.append(self.vector_tool.search(query, doc_ids=doc_ids))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            idx = 0
            if plan.use_sql:
                sql_result = results[idx] if not isinstance(results[idx], Exception) else None
                idx += 1
            if plan.use_vector:
                vector_results = results[idx] if not isinstance(results[idx], Exception) else []

        else:
            if plan.use_sql:
                sql_result = await self.sql_tool.query(query, doc_ids)
            if plan.use_vector:
                vector_results = await self.vector_tool.search(query, doc_ids=doc_ids)

        # 3. 답변 생성
        answer = await self._generate_answer(query, sql_result, vector_results)

        # 4. 출처 정리
        sources = self._collect_sources(sql_result, vector_results)

        return SearchResult(
            query=query,
            query_type=plan.query_type.value,
            sql_result=sql_result,
            vector_results=vector_results,
            answer=answer,
            sources=sources
        )

    async def _generate_answer(
        self,
        query: str,
        sql_result: dict | None,
        vector_results: list[dict]
    ) -> str:
        """컨텍스트 조합 후 Claude로 답변 생성."""

        # SQL 섹션
        if sql_result and sql_result.get("success") and sql_result.get("rows"):
            sql_section = f"SQL: {sql_result['sql']}\n결과:\n"
            for row in sql_result["rows"][:10]:
                sql_section += str(row) + "\n"
        else:
            sql_section = "해당 없음 (수치 DB 조회 결과 없음)"

        # Vector 섹션
        if vector_results:
            vector_section = "\n---\n".join(
                f"[{r['filename']} p.{r['page']}] (유사도: {r['score']})\n{r['content']}"
                for r in vector_results[:5]
            )
        else:
            vector_section = "해당 없음"

        prompt = ANSWER_PROMPT.format(
            sql_section=sql_section,
            vector_section=vector_section,
            query=query
        )

        response = await self.anthropic.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        return response.content[0].text

    def _collect_sources(
        self, sql_result: dict | None, vector_results: list[dict]
    ) -> list[dict]:
        sources = []
        if sql_result and sql_result.get("success"):
            sources.append({"type": "database", "sql": sql_result.get("sql")})
        for r in vector_results:
            sources.append({
                "type": "document",
                "filename": r.get("filename"),
                "page": r.get("page"),
                "score": r.get("score")
            })
        return sources
```

---

## 5. Phase 3 완료 체크리스트

```
[ ] QueryClassifier: 쿼리 유형 자동 분류
[ ] SQLTool: NL→SQL 생성 및 실행
[ ] VectorTool: 메타데이터 필터 검색
[ ] SearchEngine: 병렬 실행 및 결과 통합
[ ] 답변에 출처 명시 확인

테스트 질문:
[ ] "풍화토-2의 점착력은?" → SQL에서 10.0 kN/m² 반환
[ ] "앵커 안전율 기준이 뭔가?" → Vector에서 표 3.4 관련 내용 반환
[ ] "SEC-1O의 근입깊이 안전율이 허용 기준을 만족하는가?" → SQL+Vector 통합
[ ] "굴착깊이가 가장 깊은 단면은?" → SQL GROUP BY 집계
```
