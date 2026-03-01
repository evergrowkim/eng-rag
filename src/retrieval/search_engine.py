"""검색 엔진 — SQL + Vector + TreeSearch 통합."""

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

from anthropic import AsyncAnthropic
from loguru import logger

from .query_classifier import QueryClassifier, QueryType
from .reranker import Reranker
from .sql_tool import SQLTool
from .tree_tool import TreeTool
from .vector_tool import VectorTool

# 쿼리에서 SEC-XY 패턴 추출
SEC_ID_RE = re.compile(r"SEC-(\w+(?:\([^)]*\))?)")


@dataclass
class SearchResult:
    query: str
    query_type: str
    sql_result: dict | None
    vector_results: list[dict]
    tree_results: list[dict]
    answer: str
    sources: list[dict]


ANSWER_PROMPT = """
당신은 엔지니어링 설계 전문 AI입니다.

## 데이터베이스 조회 결과
{sql_section}

## 문서 검색 결과
{vector_section}

## 트리 탐색 결과
{tree_section}

## 질문
{query}

## 답변 규칙
1. 수치는 반드시 단위 포함 (예: 179.83 kN·m, 2.255, 13 kN/m²)
2. 출처를 반드시 명시: [문서명 p.페이지]
3. 기준값과 계산값 구분
4. 판정이 필요하면 명확하게: O.K / N.G
5. 확실하지 않으면 "확인 필요" — 절대 추측 금지
6. 답변은 간결하게 (3~5문장)
7. 질문에 특정 단면 ID(SEC-XY)가 언급되면, 반드시 해당 단면의 데이터만 사용
"""


class SearchEngine:

    def __init__(
        self,
        db_path: str,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
    ) -> None:
        from openai import AsyncOpenAI

        from ..common.qdrant_client import get_qdrant_client

        self.classifier = QueryClassifier()
        self.anthropic = AsyncAnthropic()

        qdrant = get_qdrant_client(host=qdrant_host, port=qdrant_port)
        openai = AsyncOpenAI()

        self.sql_tool = SQLTool(db_path, self.anthropic)
        self.vector_tool = VectorTool(qdrant, openai)
        self.tree_tool = TreeTool(self.anthropic)
        self.reranker = Reranker(
            cohere_api_key=os.environ.get("COHERE_API_KEY"),
        )

    async def search(
        self,
        query: str,
        doc_ids: list[str] | None = None,
    ) -> SearchResult:
        """질문 분류 -> 도구 선택 -> 검색 -> 답변 생성."""
        logger.info(f"검색 시작: '{query}'")

        # 0. 쿼리에서 section_id 추출
        section_ids = self._extract_section_ids(query)
        if section_ids:
            logger.info(f"쿼리에서 section_id 감지: {section_ids}")

        # 1. 분류
        plan = self.classifier.classify(query)
        logger.info(
            f"쿼리 유형: {plan.query_type}, "
            f"SQL={plan.use_sql}, Vector={plan.use_vector}, "
            f"PageIndex={plan.use_pageindex}"
        )

        # 2. 병렬/직렬 검색
        sql_result: dict | None = None
        vector_results: list[dict] = []
        tree_results: list[dict] = []

        if plan.parallel:
            tasks: list = []
            task_labels: list[str] = []

            if plan.use_sql:
                tasks.append(self.sql_tool.query(query, doc_ids))
                task_labels.append("sql")
            if plan.use_vector:
                tasks.append(
                    self.vector_tool.search(
                        query,
                        top_k=20,  # Reranking을 위해 더 많이 가져옴
                        doc_ids=doc_ids,
                        section_ids=section_ids or None,
                    )
                )
                task_labels.append("vector")
            if plan.use_pageindex:
                tasks.append(self.tree_tool.search(query, doc_ids))
                task_labels.append("tree")

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for label, result in zip(task_labels, results):
                if isinstance(result, Exception):
                    logger.error(f"{label} 검색 실패: {result}")
                    continue
                if label == "sql":
                    sql_result = result
                elif label == "vector":
                    vector_results = result
                elif label == "tree":
                    tree_results = result

        else:
            if plan.use_sql:
                try:
                    sql_result = await self.sql_tool.query(query, doc_ids)
                except Exception as e:
                    logger.error(f"SQL 검색 실패: {e}")

            if plan.use_vector:
                try:
                    vector_results = await self.vector_tool.search(
                        query,
                        top_k=20,
                        doc_ids=doc_ids,
                        section_ids=section_ids or None,
                    )
                except Exception as e:
                    logger.error(f"벡터 검색 실패: {e}")

            if plan.use_pageindex:
                try:
                    tree_results = await self.tree_tool.search(query, doc_ids)
                except Exception as e:
                    logger.error(f"트리 검색 실패: {e}")

        # 2.5. Re-ranking (벡터 결과가 5개 초과일 때)
        if len(vector_results) > 5:
            vector_results = await self.reranker.rerank(
                query, vector_results, top_n=5,
            )

        # 3. 답변 생성
        answer = await self._generate_answer(
            query, sql_result, vector_results, tree_results,
        )

        # 4. 출처 정리
        sources = self._collect_sources(sql_result, vector_results, tree_results)

        return SearchResult(
            query=query,
            query_type=plan.query_type.value,
            sql_result=sql_result,
            vector_results=vector_results,
            tree_results=tree_results,
            answer=answer,
            sources=sources,
        )

    def _extract_section_ids(self, query: str) -> list[str]:
        """쿼리에서 SEC-XY 패턴 추출."""
        matches = SEC_ID_RE.findall(query)
        return [f"SEC-{m}" for m in matches] if matches else []

    async def _generate_answer(
        self,
        query: str,
        sql_result: dict | None,
        vector_results: list[dict],
        tree_results: list[dict],
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
                f"(유사도: {r['score']}, 단면: {r.get('section_id', 'N/A')})"
                f"\n{r['content']}"
                for r in vector_results[:5]
            )
        else:
            vector_section = "해당 없음"

        # Tree 섹션
        if tree_results:
            tree_section = "\n".join(
                f"[{tr['title']}] pages={tr['pages']} "
                f"단면={tr.get('section_id', 'N/A')} "
                f"| {tr.get('summary', '')[:100]}"
                for tr in tree_results
            )
        else:
            tree_section = "해당 없음"

        prompt = ANSWER_PROMPT.format(
            sql_section=sql_section,
            vector_section=vector_section,
            tree_section=tree_section,
            query=query,
        )

        response = await self.anthropic.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )

        return response.content[0].text

    def _collect_sources(
        self,
        sql_result: dict | None,
        vector_results: list[dict],
        tree_results: list[dict],
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
                "section_id": r.get("section_id"),
            })
        for tr in tree_results:
            sources.append({
                "type": "tree",
                "title": tr.get("title"),
                "pages": tr.get("pages"),
                "section_id": tr.get("section_id"),
            })
        return sources
