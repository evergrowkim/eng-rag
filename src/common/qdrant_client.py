"""Qdrant 클라이언트 팩토리.

환경변수 QDRANT_MODE에 따라 Docker 또는 로컬 파일 모드를 선택한다.
- "docker" (기본): localhost:6333 Docker 컨테이너에 연결
- "local": data/qdrant_local/ 디렉토리에 파일 기반 저장
"""

from __future__ import annotations

import os

from loguru import logger
from qdrant_client import QdrantClient


def get_qdrant_client(
    host: str = "localhost",
    port: int = 6333,
) -> QdrantClient:
    """환경변수 기반으로 Qdrant 클라이언트를 생성한다."""
    mode = os.environ.get("QDRANT_MODE", "docker").lower()

    if mode == "local":
        local_path = os.environ.get("QDRANT_LOCAL_PATH", "data/qdrant_local")
        os.makedirs(local_path, exist_ok=True)
        logger.info(f"Qdrant 로컬 모드: {local_path}")
        return QdrantClient(path=local_path)

    logger.info(f"Qdrant Docker 모드: {host}:{port}")
    return QdrantClient(host=host, port=port)
