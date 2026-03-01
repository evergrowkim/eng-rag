"""PageIndex Tree Search 도구.

PageIndex-main 방법론 기반:
LLM이 문서 트리를 추론하여 관련 섹션을 선택한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from loguru import logger


TREE_SEARCH_PROMPT = """\
당신은 엔지니어링 문서의 계층 트리를 탐색하는 전문가입니다.
질문에 답하기 위해 필요한 노드(섹션)를 모두 찾으세요.

## 엔지니어링 전문 지식
- SEC-XY는 흙막이 단면 ID입니다. "SEC-1O"와 "SEC-1H(R)"는 완전히 다른 단면입니다.
- "검토 요약" 섹션에 해당 단면의 수치 검토 결과(안전율, 변위, 모멘트 등)가 있습니다.
- 질문에 특정 단면 ID가 언급되면, 반드시 해당 단면의 노드만 선택하세요.

## 질문
{query}

## 문서 트리 구조
{tree_json}

## 응답 형식 (JSON만 반환)
{{
  "thinking": "어떤 노드가 관련 있는지에 대한 추론",
  "node_list": ["0003", "0005"]
}}
"""


class TreeTool:
    """PageIndex 트리 기반 섹션 탐색."""

    def __init__(
        self,
        anthropic_client: AsyncAnthropic,
        index_dir: str = "data/indexes",
    ) -> None:
        self.anthropic = anthropic_client
        self.index_dir = Path(index_dir)

    async def search(
        self,
        query: str,
        doc_ids: list[str] | None = None,
    ) -> list[dict]:
        """트리 탐색으로 관련 섹션 반환.

        Returns:
            [{"node_id", "title", "node_type", "pages", "summary", "section_id"}]
        """
        if not doc_ids:
            # doc_ids 없으면 모든 트리 파일 탐색
            doc_ids = self._discover_doc_ids()

        all_results: list[dict] = []

        for doc_id in doc_ids:
            tree_data = self._load_tree(doc_id)
            if not tree_data:
                continue

            nodes = tree_data.get("tree", [])
            if not nodes:
                continue

            # 트리를 간결하게 직렬화 (LLM 입력용)
            tree_compact = self._compact_tree(nodes)

            # LLM에게 트리 탐색 요청
            selected_ids = await self._llm_tree_search(query, tree_compact)

            if not selected_ids:
                logger.debug(f"트리 탐색 결과 없음: {doc_id}")
                continue

            # 선택된 노드의 상세 정보 수집
            all_nodes = self._flatten_nodes(nodes)
            node_map = {n["node_id"]: n for n in all_nodes}

            for nid in selected_ids:
                node = node_map.get(nid)
                if node:
                    # section_id 추출
                    sec_match = re.search(r"SEC-(\w+(?:\([^)]*\))?)", node.get("title", ""))
                    section_id = f"SEC-{sec_match.group(1)}" if sec_match else None

                    all_results.append({
                        "doc_id": doc_id,
                        "node_id": nid,
                        "title": node.get("title", ""),
                        "node_type": node.get("node_type", ""),
                        "pages": node.get("pages", []),
                        "summary": node.get("summary", ""),
                        "section_id": section_id,
                    })

        logger.info(f"트리 탐색 완료: {len(all_results)}개 노드 선택")
        return all_results

    async def _llm_tree_search(
        self, query: str, tree_json: str
    ) -> list[str]:
        """LLM에게 트리 탐색을 요청하고 node_id 리스트 반환."""
        prompt = TREE_SEARCH_PROMPT.format(query=query, tree_json=tree_json)

        try:
            response = await self.anthropic.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            # JSON 파싱
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                node_list = data.get("node_list", [])
                logger.debug(f"트리 탐색: {data.get('thinking', '')[:100]}")
                return [str(n) for n in node_list]

        except Exception as e:
            logger.warning(f"트리 탐색 LLM 호출 실패: {e}")

        return []

    def _load_tree(self, doc_id: str) -> dict[str, Any] | None:
        """트리 JSON 파일 로드."""
        tree_path = self.index_dir / f"{doc_id}_tree.json"
        if not tree_path.exists():
            return None
        try:
            with open(tree_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"트리 로드 실패 ({doc_id}): {e}")
            return None

    def _discover_doc_ids(self) -> list[str]:
        """인덱스 디렉토리에서 모든 doc_id 발견."""
        if not self.index_dir.exists():
            return []
        return [
            p.stem.replace("_tree", "")
            for p in self.index_dir.glob("*_tree.json")
        ]

    def _compact_tree(self, nodes: list[dict], depth: int = 0) -> str:
        """트리를 LLM 입력용 간결한 텍스트로 변환."""
        lines: list[str] = []
        indent = "  " * depth
        for node in nodes:
            nid = node.get("node_id", "")
            title = node.get("title", "")
            ntype = node.get("node_type", "")
            pages = node.get("pages", [])
            summary = node.get("summary", "")

            line = f"{indent}[{nid}] ({ntype}) {title} | pages={pages}"
            if summary:
                line += f" | {summary[:80]}"
            lines.append(line)

            children = node.get("children", [])
            if children:
                lines.append(self._compact_tree(children, depth + 1))

        return "\n".join(lines)

    def _flatten_nodes(self, nodes: list[dict]) -> list[dict]:
        """트리를 평탄한 리스트로 변환."""
        result: list[dict] = []
        for node in nodes:
            result.append(node)
            result.extend(self._flatten_nodes(node.get("children", [])))
        return result
