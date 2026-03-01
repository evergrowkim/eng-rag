from loguru import logger
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue


class VectorTool:

    def __init__(
        self, qdrant_client: QdrantClient, openai_client: AsyncOpenAI
    ) -> None:
        self.qdrant = qdrant_client
        self.openai = openai_client
        self.collection = "doaz_eng_rag"

    async def search(
        self,
        query: str,
        top_k: int = 5,
        doc_ids: list[str] | None = None,
        block_types: list[str] | None = None,
        section_ids: list[str] | None = None,
    ) -> list[dict]:
        """의미 기반 벡터 검색.

        Args:
            section_ids: 특정 단면만 검색 (예: ["SEC-1O", "SEC-2A"])

        Returns: [{"content", "page", "doc_id", "block_type", "score", "filename", "section_id"}]
        """
        logger.debug(f"벡터 검색 시작: '{query}' (top_k={top_k}, sections={section_ids})")

        # 1. 쿼리 임베딩
        response = await self.openai.embeddings.create(
            model="text-embedding-3-large",
            input=query,
        )
        query_vector = response.data[0].embedding

        # 2. 필터 구성
        filters: list[FieldCondition] = []
        if doc_ids:
            filters.append(FieldCondition(
                key="doc_id",
                match=MatchAny(any=doc_ids),
            ))
        if block_types:
            filters.append(FieldCondition(
                key="block_type",
                match=MatchAny(any=block_types),
            ))
        if section_ids:
            filters.append(FieldCondition(
                key="section_id",
                match=MatchAny(any=section_ids),
            ))

        qdrant_filter = Filter(must=filters) if filters else None

        # 3. 검색 (qdrant-client >= 1.12: query_points)
        results = self.qdrant.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
        )

        points = results.points
        logger.info(f"벡터 검색 완료: {len(points)}건 반환")

        return [
            {
                "content": r.payload.get("content", ""),
                "page": r.payload.get("page_number"),
                "doc_id": r.payload.get("doc_id"),
                "block_type": r.payload.get("block_type"),
                "score": round(r.score, 4),
                "filename": r.payload.get("filename"),
                "section_id": r.payload.get("section_id"),
            }
            for r in points
        ]
