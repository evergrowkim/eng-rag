"""Qdrant 컬렉션 설정.

컬렉션 생성 및 페이로드 인덱스를 설정한다.
멱등성 보장: 이미 존재하는 컬렉션은 건너뛴다.
"""

from __future__ import annotations

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, VectorParams


class IndexingError(Exception):
    """인덱싱 파이프라인 오류."""


COLLECTION_NAME: str = "doaz_eng_rag"
VECTOR_SIZE: int = 3072  # text-embedding-3-large


def setup_collection(client: QdrantClient) -> QdrantClient:
    """컬렉션 생성 및 페이로드 인덱스 설정.

    페이로드 인덱스: 필터 검색 성능 향상
    - doc_id: 특정 문서 내 검색
    - block_type: 유형별 필터
    - page_number: 페이지 범위 검색
    """
    try:
        collections = {c.name for c in client.get_collections().collections}

        if COLLECTION_NAME in collections:
            logger.info(f"컬렉션 이미 존재: {COLLECTION_NAME}")
            return client

        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )

        # 페이로드 인덱스 생성 (필터 성능)
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="doc_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="block_type",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="page_number",
            field_schema=PayloadSchemaType.INTEGER,
        )
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="section_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )

        logger.info(f"컬렉션 생성 완료: {COLLECTION_NAME} (vector_size={VECTOR_SIZE})")

    except Exception as e:
        logger.error(f"Qdrant 컬렉션 설정 실패: {e}")
        raise IndexingError(str(e)) from e

    return client
