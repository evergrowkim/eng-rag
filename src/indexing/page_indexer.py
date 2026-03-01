"""PageIndex 계층 트리 생성기.

문서 계층 구조를 트리로 표현한다.
전략:
  1. 정규식으로 헤더 패턴 감지 (1차, 빠름)
  2. CHECK_SECTION에 대해 LLM으로 요약 추가 (2차, 느리지만 정확)

엔지니어링 보고서 헤더 패턴:
  - "01.", "02." → 챕터
  - "3-1.", "3-2." → 섹션
  - "3-1-1." → 서브섹션
  - "SEC-1O 검토 요약" → 단면 검토 섹션
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from loguru import logger

from ..ingestion.layout_parser import BlockType, ParsedDocument

# 한국 엔지니어링 보고서 헤더 패턴
CHAPTER_RE = re.compile(r"^(?:0?\d+)\.\s+[가-힣A-Za-z]")
SECTION_RE = re.compile(r"^\d+-\d+\.\s+[가-힣A-Za-z]")
SUBSECTION_RE = re.compile(r"^\d+-\d+-\d+\.\s+[가-힣A-Za-z]")
SEC_CHECK_RE = re.compile(r"SEC-(\w+(?:\([^)]*\))?)\s+검토\s*요약")


@dataclass
class TreeNode:
    """문서 계층 트리 노드."""

    title: str
    node_id: str
    node_type: str  # CHAPTER / SECTION / SUBSECTION / CHECK_SECTION
    summary: str = ""
    pages: list[int] = field(default_factory=list)
    has_tables: bool = False
    has_check_results: bool = False
    critical_values: bool = False  # 핵심 설계값 포함 여부
    children: list[TreeNode] = field(default_factory=list)


class PageIndexer:
    """문서 계층 구조를 트리로 표현."""

    def __init__(
        self,
        anthropic_client: AsyncAnthropic,
        index_dir: str = "data/indexes",
    ) -> None:
        self.anthropic = anthropic_client
        self.index_dir = Path(index_dir)

    async def build_tree(self, doc: ParsedDocument, doc_id: str) -> dict[str, Any]:
        """문서 계층 트리 생성."""
        logger.info(f"PageIndex 트리 생성 시작: {doc.filename}")

        # 헤더 기반 구조 감지
        structure = self._detect_structure(doc)

        # LLM으로 섹션 요약 생성
        structure = await self._add_summaries(structure, doc)

        tree: dict[str, Any] = {
            "doc_id": doc_id,
            "filename": doc.filename,
            "tree": [asdict(node) for node in structure],
        }

        # 파일로 저장
        try:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            tree_path = self.index_dir / f"{doc_id}_tree.json"
            with open(tree_path, "w", encoding="utf-8") as f:
                json.dump(tree, f, ensure_ascii=False, indent=2)
            logger.info(f"트리 생성 완료: {len(structure)}개 최상위 노드, 저장: {tree_path}")
        except OSError as e:
            logger.error(f"트리 JSON 저장 실패: {e}")

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
                    if block.block_type == BlockType.SOIL_TABLE:
                        current_section.has_tables = True
                        current_section.critical_values = True
                    elif block.block_type == BlockType.CHECK_RESULT:
                        current_section.has_check_results = True
                    elif block.block_type == BlockType.TABLE:
                        current_section.has_tables = True
                continue

            for line in block.content.split("\n"):
                line = line.strip()
                if not line:
                    continue

                # 단면 검토 섹션 (가장 구체적 패턴 먼저)
                if SEC_CHECK_RE.search(line):
                    node = TreeNode(
                        title=line,
                        node_id=new_id(),
                        node_type="CHECK_SECTION",
                        pages=[block.page],
                        has_check_results=True,
                    )
                    if current_chapter:
                        current_chapter.children.append(node)
                    else:
                        nodes.append(node)
                    current_section = node

                # 챕터
                elif CHAPTER_RE.match(line):
                    node = TreeNode(
                        title=line,
                        node_id=new_id(),
                        node_type="CHAPTER",
                        pages=[block.page],
                    )
                    nodes.append(node)
                    current_chapter = node
                    current_section = None

                # 섹션
                elif SECTION_RE.match(line) and current_chapter:
                    node = TreeNode(
                        title=line,
                        node_id=new_id(),
                        node_type="SECTION",
                        pages=[block.page],
                    )
                    current_chapter.children.append(node)
                    current_section = node

                # 서브섹션
                elif SUBSECTION_RE.match(line) and current_section:
                    node = TreeNode(
                        title=line,
                        node_id=new_id(),
                        node_type="SUBSECTION",
                        pages=[block.page],
                    )
                    current_section.children.append(node)

        return nodes

    async def _add_summaries(
        self, nodes: list[TreeNode], doc: ParsedDocument
    ) -> list[TreeNode]:
        """각 CHECK_SECTION에 LLM 요약 추가."""
        all_nodes = self._flatten_nodes(nodes)
        check_nodes = [n for n in all_nodes if n.node_type == "CHECK_SECTION"]

        for node in check_nodes[:10]:  # 최대 10개
            relevant_blocks = [b for b in doc.blocks if b.page in node.pages]
            context = "\n".join(b.content[:500] for b in relevant_blocks[:3])

            if not context.strip():
                node.summary = ""
                continue

            try:
                response = await self.anthropic.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=150,
                    messages=[{
                        "role": "user",
                        "content": (
                            "다음 엔지니어링 단면 검토 내용을 "
                            "1~2문장으로 요약하세요:\n"
                            f"{context}"
                        ),
                    }],
                )
                node.summary = response.content[0].text
                logger.debug(f"요약 생성: {node.node_id}")
            except Exception as e:
                logger.warning(f"요약 생성 실패 (node={node.node_id}): {e}")
                node.summary = ""

        return nodes

    def _flatten_nodes(self, nodes: list[TreeNode]) -> list[TreeNode]:
        """트리 노드를 평탄화."""
        result: list[TreeNode] = []
        for node in nodes:
            result.append(node)
            result.extend(self._flatten_nodes(node.children))
        return result
