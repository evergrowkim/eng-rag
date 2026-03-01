# 04. 인덱싱 (Vector + PageIndex)

## Phase 2 구현 목표
파싱된 블록 → Qdrant 벡터 인덱싱 → 기본 의미 검색 가능

---

## 1. 청크 전략 (엔지니어링 특화)

```
일반 문서:   무조건 고정 크기로 자름 (chunk_size=500)
엔지니어링:  블록 유형별로 다른 전략 적용

┌─────────────────────┬──────────────────────────────────────────┐
│ 블록 유형           │ 청크 전략                                 │
├─────────────────────┼──────────────────────────────────────────┤
│ TEXT (일반 텍스트)  │ 섹션 헤더 기준 분리 (헤더 + 본문 함께)   │
│ TABLE (일반 테이블) │ 테이블 전체를 하나의 청크로              │
│ SOIL_TABLE (지반정수)│ 행 단위로 분리 + 전체 테이블 청크 추가  │
│ CHECK_RESULT        │ 단면 섹션 전체를 하나의 청크로           │
│ SUNEX_OUTPUT        │ Step 단위로 분리                         │
└─────────────────────┴──────────────────────────────────────────┘
```

---

## 2. Qdrant 컬렉션 설계

```python
# src/indexing/qdrant_setup.py

from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance, PayloadSchemaType,
    CreateCollection
)


COLLECTION_NAME = "doaz_eng_rag"
VECTOR_SIZE = 3072  # text-embedding-3-large


def setup_collection(client: QdrantClient):
    """
    컬렉션 생성 및 페이로드 인덱스 설정.
    
    페이로드 인덱스: 필터 검색 성능 향상
    - doc_id: 특정 문서 내 검색
    - block_type: 유형별 필터
    - page_number: 페이지 범위 검색
    - section_path: 섹션 내 검색
    """
    collections = [c.name for c in client.get_collections().collections]

    if COLLECTION_NAME not in collections:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE
            )
        )

        # 페이로드 인덱스 생성 (필터 성능)
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="doc_id",
            field_schema=PayloadSchemaType.KEYWORD
        )
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="block_type",
            field_schema=PayloadSchemaType.KEYWORD
        )
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="page_number",
            field_schema=PayloadSchemaType.INTEGER
        )

    return client
```

---

## 3. 벡터 인덱서

```python
# src/indexing/vector_indexer.py

import uuid
from typing import Any
from loguru import logger
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from ..ingestion.layout_parser import ParsedDocument, ParsedBlock, BlockType


class VectorIndexer:
    """
    파싱된 블록을 임베딩하여 Qdrant에 저장.
    
    임베딩 모델: text-embedding-3-large (3072차원)
    배치 크기: 100 (API 제한 고려)
    """

    BATCH_SIZE = 100
    EMBEDDING_MODEL = "text-embedding-3-large"

    def __init__(self, qdrant_client: QdrantClient, openai_client: AsyncOpenAI):
        self.qdrant = qdrant_client
        self.openai = openai_client
        self.collection = "doaz_eng_rag"

    async def index_document(self, doc: ParsedDocument, doc_id: str) -> int:
        """문서의 모든 블록을 인덱싱. 저장된 포인트 수 반환."""
        chunks = self._prepare_chunks(doc, doc_id)
        logger.info(f"인덱싱 시작: {len(chunks)}개 청크")

        total = 0
        for i in range(0, len(chunks), self.BATCH_SIZE):
            batch = chunks[i:i + self.BATCH_SIZE]
            texts = [c["text"] for c in batch]

            # 임베딩 생성
            response = await self.openai.embeddings.create(
                model=self.EMBEDDING_MODEL,
                input=texts
            )
            vectors = [r.embedding for r in response.data]

            # Qdrant 저장
            points = [
                PointStruct(
                    id=chunk["id"],
                    vector=vector,
                    payload=chunk["payload"]
                )
                for chunk, vector in zip(batch, vectors, strict=True)
            ]

            self.qdrant.upsert(
                collection_name=self.collection,
                points=points
            )
            total += len(points)
            logger.debug(f"  배치 {i//self.BATCH_SIZE + 1}: {len(points)}개 저장")

        logger.info(f"인덱싱 완료: {total}개 포인트")
        return total

    def _prepare_chunks(
        self, doc: ParsedDocument, doc_id: str
    ) -> list[dict[str, Any]]:
        """블록을 청크로 변환. 유형별 다른 전략 적용."""
        chunks = []

        for block in doc.blocks:
            chunk_text = self._prepare_chunk_text(block)
            if not chunk_text.strip():
                continue

            payload = {
                "doc_id": doc_id,
                "filename": doc.filename,
                "block_type": block.block_type.value,
                "page_number": block.page,
                "content": block.content[:500],  # 페이로드 미리보기
                "has_table": block.table_data is not None,
                "check_result": block.check_values,
            }

            chunks.append({
                "id": str(uuid.uuid4()),
                "text": chunk_text,
                "payload": payload
            })

            # SOIL_TABLE: 행 단위 추가 청크
            if block.block_type == BlockType.SOIL_TABLE:
                for row in (block.table_data or []):
                    row_text = self._soil_row_to_sentence(row)
                    if row_text:
                        chunks.append({
                            "id": str(uuid.uuid4()),
                            "text": row_text,
                            "payload": {**payload, "is_row_chunk": True}
                        })

        return chunks

    def _prepare_chunk_text(self, block: ParsedBlock) -> str:
        """
        검색에 최적화된 텍스트 생성.
        임베딩할 텍스트는 자연어에 가까울수록 좋음.
        """
        if block.block_type == BlockType.SOIL_TABLE:
            # 지반정수 테이블 → 자연어 설명으로 변환
            return self._soil_table_to_natural(block)

        elif block.block_type == BlockType.CHECK_RESULT:
            cv = block.check_values or {}
            return (
                f"검토결과: 계산값 {cv.get('calculated')} "
                f"허용값 {cv.get('allowable')} "
                f"결과 {cv.get('result')} "
                f"(활용률 {cv.get('utilization', 'N/A')})"
            )

        return block.content

    def _soil_table_to_natural(self, block: ParsedBlock) -> str:
        """지반정수 테이블을 자연어로 변환."""
        lines = ["지반정수 요약표:"]
        for row in (block.table_data or []):
            layer = row.get("지층", "")
            n = row.get("N치", "")
            gamma = row.get("단위중량", "")
            c = row.get("점착력", "")
            phi = row.get("내부마찰각", "")
            kh = row.get("수평지반반력계수", "")
            lines.append(
                f"{layer}: N치={n}, 단위중량={gamma}kN/m³, "
                f"점착력={c}kN/m², 내부마찰각={phi}°, 수평반력계수={kh}kN/m³"
            )
        return "\n".join(lines)

    def _soil_row_to_sentence(self, row: dict) -> str:
        """개별 지반정수 행을 문장으로 변환."""
        layer = row.get("지층", "")
        if not layer:
            return ""
        parts = []
        for key, unit in [("N치", ""), ("단위중량", "kN/m³"),
                          ("점착력", "kN/m²"), ("내부마찰각", "°")]:
            val = row.get(key, "")
            if val:
                parts.append(f"{key} {val}{unit}")
        return f"{layer} 지층의 지반정수: " + ", ".join(parts)
```

---

## 4. PageIndex (계층 트리) 생성기

```python
# src/indexing/page_indexer.py

import json
from dataclasses import dataclass, field, asdict
from typing import Any
import re
from anthropic import AsyncAnthropic
from loguru import logger

from ..ingestion.layout_parser import ParsedDocument, BlockType


@dataclass
class TreeNode:
    title: str
    node_id: str
    node_type: str          # CHAPTER / SECTION / SUBSECTION / CHECK_SECTION
    summary: str = ""
    pages: list[int] = field(default_factory=list)
    has_tables: bool = False
    has_check_results: bool = False
    critical_values: bool = False   # 핵심 설계값 포함 여부
    children: list["TreeNode"] = field(default_factory=list)


class PageIndexer:
    """
    문서 계층 구조를 트리로 표현.
    
    전략:
    1. 정규식으로 헤더 패턴 감지 (1차, 빠름)
    2. 헤더 감지 실패 시 LLM으로 구조 추론 (2차, 느리지만 정확)
    
    엔지니어링 보고서 헤더 패턴:
    - "01.", "02.", "03." → 챕터
    - "3-1.", "3-2." → 섹션
    - "3-1-1.", "3-2-1." → 서브섹션
    - "SEC-1O", "SEC-2A" → 단면 검토 섹션
    """

    # 한국 엔지니어링 보고서 헤더 패턴
    CHAPTER_RE = re.compile(r"^(?:0?\d+)\.\s+[가-힣A-Za-z]")
    SECTION_RE = re.compile(r"^\d+-\d+\.\s+[가-힣A-Za-z]")
    SUBSECTION_RE = re.compile(r"^\d+-\d+-\d+\.\s+[가-힣A-Za-z]")
    SEC_CHECK_RE = re.compile(r"SEC-(\w+)\s+검토\s*요약")

    def __init__(self, anthropic_client: AsyncAnthropic):
        self.anthropic = anthropic_client

    async def build_tree(self, doc: ParsedDocument, doc_id: str) -> dict:
        """문서 계층 트리 생성."""
        logger.info(f"PageIndex 트리 생성 시작: {doc.filename}")

        # 헤더 기반 구조 감지
        structure = self._detect_structure(doc)

        # LLM으로 섹션 요약 생성
        structure = await self._add_summaries(structure, doc)

        tree = {
            "doc_id": doc_id,
            "filename": doc.filename,
            "tree": [asdict(node) for node in structure]
        }

        # 파일로 저장
        tree_path = f"data/indexes/{doc_id}_tree.json"
        with open(tree_path, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)

        logger.info(f"트리 생성 완료: {len(structure)}개 최상위 노드")
        return tree

    def _detect_structure(self, doc: ParsedDocument) -> list[TreeNode]:
        """정규식으로 문서 구조 감지."""
        nodes: list[TreeNode] = []
        node_id_counter = [0]

        def new_id() -> str:
            node_id_counter[0] += 1
            return f"{node_id_counter[0]:04d}"

        current_chapter: TreeNode | None = None
        current_section: TreeNode | None = None

        for block in doc.blocks:
            if block.block_type != BlockType.TEXT:
                # 비텍스트 블록을 현재 섹션에 속성으로 기록
                if current_section:
                    if block.block_type.value == "soil_table":
                        current_section.has_tables = True
                        current_section.critical_values = True
                    elif block.block_type.value == "check_result":
                        current_section.has_check_results = True
                continue

            for line in block.content.split("\n"):
                line = line.strip()
                if not line:
                    continue

                # 단면 검토 섹션
                if self.SEC_CHECK_RE.search(line):
                    node = TreeNode(
                        title=line,
                        node_id=new_id(),
                        node_type="CHECK_SECTION",
                        pages=[block.page],
                        has_check_results=True
                    )
                    if current_chapter:
                        current_chapter.children.append(node)
                    else:
                        nodes.append(node)
                    current_section = node

                # 챕터
                elif self.CHAPTER_RE.match(line):
                    node = TreeNode(
                        title=line, node_id=new_id(),
                        node_type="CHAPTER", pages=[block.page]
                    )
                    nodes.append(node)
                    current_chapter = node
                    current_section = None

                # 섹션
                elif self.SECTION_RE.match(line) and current_chapter:
                    node = TreeNode(
                        title=line, node_id=new_id(),
                        node_type="SECTION", pages=[block.page]
                    )
                    current_chapter.children.append(node)
                    current_section = node

                # 서브섹션
                elif self.SUBSECTION_RE.match(line) and current_section:
                    node = TreeNode(
                        title=line, node_id=new_id(),
                        node_type="SUBSECTION", pages=[block.page]
                    )
                    current_section.children.append(node)

        return nodes

    async def _add_summaries(
        self, nodes: list[TreeNode], doc: ParsedDocument
    ) -> list[TreeNode]:
        """각 섹션에 LLM 요약 추가."""
        # 배치로 요약 생성 (비용 절감)
        all_nodes = self._flatten_nodes(nodes)

        # CHECK_SECTION만 요약 (가장 중요)
        check_nodes = [n for n in all_nodes if n.node_type == "CHECK_SECTION"]

        for node in check_nodes[:10]:  # 최대 10개
            relevant_blocks = [
                b for b in doc.blocks if b.page in node.pages
            ]
            context = "\n".join(b.content[:500] for b in relevant_blocks[:3])

            response = await self.anthropic.messages.create(
                model="claude-haiku-4-5-20251001",  # 빠르고 저렴한 모델
                max_tokens=150,
                messages=[{
                    "role": "user",
                    "content": f"다음 엔지니어링 단면 검토 내용을 1~2문장으로 요약하세요:\n{context}"
                }]
            )
            node.summary = response.content[0].text

        return nodes

    def _flatten_nodes(self, nodes: list[TreeNode]) -> list[TreeNode]:
        result = []
        for node in nodes:
            result.append(node)
            result.extend(self._flatten_nodes(node.children))
        return result
```

---

## 5. 인덱싱 파이프라인 통합

```python
# src/indexing/indexing_pipeline.py

from loguru import logger
from qdrant_client import QdrantClient
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from ..ingestion.layout_parser import ParsedDocument
from .vector_indexer import VectorIndexer
from .page_indexer import PageIndexer
from .qdrant_setup import setup_collection


class IndexingPipeline:

    def __init__(
        self,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333
    ):
        self.qdrant = setup_collection(
            QdrantClient(host=qdrant_host, port=qdrant_port)
        )
        self.openai = AsyncOpenAI()
        self.anthropic = AsyncAnthropic()
        self.vector_indexer = VectorIndexer(self.qdrant, self.openai)
        self.page_indexer = PageIndexer(self.anthropic)

    async def index(self, doc: ParsedDocument, doc_id: str) -> dict:
        logger.info(f"=== 인덱싱 시작: {doc_id} ===")

        # 1. 벡터 인덱싱
        point_count = await self.vector_indexer.index_document(doc, doc_id)

        # 2. PageIndex 트리 생성
        tree = await self.page_indexer.build_tree(doc, doc_id)

        return {
            "doc_id": doc_id,
            "vector_points": point_count,
            "tree_nodes": len(tree.get("tree", [])),
        }
```

---

## 6. Phase 2 완료 체크리스트

```
[ ] Qdrant 컬렉션 생성 및 인덱스 설정
[ ] VectorIndexer: 블록 → 임베딩 → Qdrant 저장
[ ] SOIL_TABLE 행 단위 청크 추가
[ ] PageIndexer: 헤더 기반 트리 생성
[ ] CHECK_SECTION 요약 자동 생성
[ ] 기본 벡터 검색 테스트:
    "앵커 안전율 기준" 검색 → 표 3.4 관련 청크 상위 반환
[ ] 메타데이터 필터 테스트:
    doc_id 필터로 특정 문서 내 검색
[ ] 트리 JSON 파일 생성 확인
```
