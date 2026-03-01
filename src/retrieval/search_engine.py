import asyncio
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from loguru import logger

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
        qdrant_port: int = 6333,
    ) -> None:
        from openai import AsyncOpenAI
        from qdrant_client import QdrantClient

        self.classifier = QueryClassifier()
        self.anthropic = AsyncAnthropic()

        qdrant = QdrantClient(host=qdrant_host, port=qdrant_port)
        openai = AsyncOpenAI()

        self.sql_tool = SQLTool(db_path, self.anthropic)
        self.vector_tool = VectorTool(qdrant, openai)

    async def search(
        self,
        query: str,
        doc_ids: list[str] | None = None,
    ) -> SearchResult:
        """질문 분류 → 도구 선택 → 검색 → 답변 생성."""
        logger.info(f"검색 시작: '{query}'")

        # 1. 분류
        plan = self.classifier.classify(query)
        logger.info(
            f"쿼리 유형: {plan.query_type}, "
            f"SQL={plan.use_sql}, Vector={plan.use_vector}"
        )

        # 2. 병렬/직렬 검색
        sql_result: dict | None = None
        vector_results: list[dict] = []

        if plan.parallel:
            tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
            if plan.use_sql:
                tasks.append(self.sql_tool.query(query, doc_ids))
            if plan.use_vector:
                tasks.append(self.vector_tool.search(query, doc_ids=doc_ids))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            idx = 0
            if plan.use_sql:
                sql_result = (
                    results[idx]
                    if not isinstance(results[idx], Exception)
                    else None
                )
                if isinstance(results[idx], Exception):
                    logger.error(f"SQL 검색 실패: {results[idx]}")
                idx += 1
            if plan.use_vector:
                vector_results = (
                    results[idx]
                    if not isinstance(results[idx], Exception)
                    else []
                )
                if isinstance(results[idx], Exception):
                    logger.error(f"벡터 검색 실패: {results[idx]}")

        else:
            if plan.use_sql:
                sql_result = await self.sql_tool.query(query, doc_ids)
            if plan.use_vector:
                vector_results = await self.vector_tool.search(
                    query, doc_ids=doc_ids
                )

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
            sources=sources,
        )

    async def _generate_answer(
        self,
        query: str,
        sql_result: dict | None,
        vector_results: list[dict],
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
                f"[{r['filename']} p.{r['page']}] "
                f"(유사도: {r['score']})\n{r['content']}"
                for r in vector_results[:5]
            )
        else:
            vector_section = "해당 없음"

        prompt = ANSWER_PROMPT.format(
            sql_section=sql_section,
            vector_section=vector_section,
            query=query,
        )

        response = await self.anthropic.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )

        return response.content[0].text

    def _collect_sources(
        self, sql_result: dict | None, vector_results: list[dict]
    ) -> list[dict]:
        sources: list[dict] = []
        if sql_result and sql_result.get("success"):
            sources.append({
                "type": "database",
                "sql": sql_result.get("sql"),
            })
        for r in vector_results:
            sources.append({
                "type": "document",
                "filename": r.get("filename"),
                "page": r.get("page"),
                "score": r.get("score"),
            })
        return sources
