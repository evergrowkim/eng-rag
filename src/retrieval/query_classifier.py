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
    NUMERICAL_SIGNALS: list[str] = [
        r"\d+\s*(m|mm|kN|MPa|kPa|°)",  # 단위 포함 숫자
        "몇", "얼마", "값", "수치", "결과", "크기",
        "최대", "최소", "평균", "합계", "총", "전체",
        "안전율", "변위", "깊이", "하중", "응력", "모멘트",
        # DB 컬럼 관련 지반/구조 정수
        "점착력", "마찰각", "단위중량", "N값",
        "근입", "배면", "굴착", "철근",
        "인장력", "설계력", "허용응력",
    ]

    # 적합성 신호어
    COMPLIANCE_SIGNALS: list[str] = [
        "만족", "충족", "적합", "준수", "부합",
        "위반", "초과", "미달", "이하", "이상",
        "O.K", "N.G", "검토", "판정",
    ]

    # 비교 신호어
    COMPARATIVE_SIGNALS: list[str] = [
        "비교", "차이", "vs", "대비", "versus",
        "어느", "가장", "제일", "더", "덜",
    ]

    # 멀티홉 신호어
    MULTI_HOP_SIGNALS: list[str] = [
        "근거", "이유", "왜", "어떻게 계산",
        "산정 방법", "적용 기준",
    ]

    def classify(self, query: str) -> RoutingPlan:
        logger.debug(f"쿼리 분류 시작: '{query}'")

        # 우선순위 순서로 분류
        if self._matches(query, self.MULTI_HOP_SIGNALS):
            logger.info(f"쿼리 유형: MULTI_HOP")
            return RoutingPlan(
                query_type=QueryType.MULTI_HOP,
                use_sql=True, use_vector=True, use_pageindex=True,
                parallel=True, priority="equal",
            )

        if self._matches(query, self.COMPARATIVE_SIGNALS):
            logger.info(f"쿼리 유형: COMPARATIVE")
            return RoutingPlan(
                query_type=QueryType.COMPARATIVE,
                use_sql=True, use_vector=True, use_pageindex=False,
                parallel=True, priority="sql_first",
            )

        if self._matches(query, self.COMPLIANCE_SIGNALS):
            logger.info(f"쿼리 유형: COMPLIANCE")
            return RoutingPlan(
                query_type=QueryType.COMPLIANCE,
                use_sql=True, use_vector=True, use_pageindex=False,
                parallel=True, priority="sql_first",
            )

        if self._matches(query, self.NUMERICAL_SIGNALS):
            logger.info(f"쿼리 유형: NUMERICAL")
            return RoutingPlan(
                query_type=QueryType.NUMERICAL,
                use_sql=True, use_vector=False, use_pageindex=False,
                parallel=False, priority="sql_first",
            )

        # 기본: 개념형
        logger.info(f"쿼리 유형: CONCEPTUAL")
        return RoutingPlan(
            query_type=QueryType.CONCEPTUAL,
            use_sql=False, use_vector=True, use_pageindex=True,
            parallel=False, priority="vector_first",
        )

    def _matches(self, text: str, signals: list[str]) -> bool:
        for signal in signals:
            if signal.startswith(r"\\") or "(" in signal:
                if re.search(signal, text):
                    return True
            elif signal in text:
                return True
        return False
