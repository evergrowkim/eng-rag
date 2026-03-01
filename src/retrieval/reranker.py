"""검색 결과 Re-ranking.

Cohere Rerank API 사용 가능 시 활용, 불가 시 LLM 기반 fallback.
"""

from __future__ import annotations

from loguru import logger

try:
    import cohere
    COHERE_AVAILABLE = True
except ImportError:
    COHERE_AVAILABLE = False


class Reranker:
    """벡터 검색 결과를 재정렬하여 정확도를 높인다."""

    def __init__(self, cohere_api_key: str | None = None) -> None:
        self.client = None
        if COHERE_AVAILABLE and cohere_api_key:
            try:
                self.client = cohere.Client(cohere_api_key)
                logger.info("Cohere Reranker 초기화 완료")
            except Exception as e:
                logger.warning(f"Cohere 초기화 실패, LLM fallback 사용: {e}")

    async def rerank(
        self,
        query: str,
        results: list[dict],
        top_n: int = 5,
    ) -> list[dict]:
        """검색 결과를 쿼리 관련성 기준으로 재정렬.

        Args:
            query: 사용자 질문
            results: 벡터 검색 결과 리스트 (각 항목에 "content" 키 필요)
            top_n: 반환할 상위 결과 수

        Returns:
            재정렬된 결과 리스트
        """
        if not results:
            return results

        if len(results) <= top_n:
            return results

        if self.client:
            return await self._cohere_rerank(query, results, top_n)

        # Cohere 없으면 score 기반 정렬 (기본 동작)
        return self._score_rerank(query, results, top_n)

    async def _cohere_rerank(
        self, query: str, results: list[dict], top_n: int
    ) -> list[dict]:
        """Cohere Rerank API 사용."""
        try:
            documents = [r.get("content", "") for r in results]

            response = self.client.rerank(
                model="rerank-v3.5",
                query=query,
                documents=documents,
                top_n=top_n,
            )

            reranked = []
            for item in response.results:
                original = results[item.index]
                original["rerank_score"] = round(item.relevance_score, 4)
                reranked.append(original)

            logger.info(f"Cohere 재정렬 완료: {len(results)}건 → {len(reranked)}건")
            return reranked

        except Exception as e:
            logger.warning(f"Cohere 재정렬 실패, score fallback: {e}")
            return self._score_rerank(query, results, top_n)

    def _score_rerank(
        self, query: str, results: list[dict], top_n: int
    ) -> list[dict]:
        """벡터 유사도 score 기반 정렬 (fallback)."""
        sorted_results = sorted(
            results,
            key=lambda r: r.get("score", 0),
            reverse=True,
        )
        return sorted_results[:top_n]
