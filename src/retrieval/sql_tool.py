import re

import aiosqlite
from anthropic import AsyncAnthropic
from loguru import logger


# 시스템이 알고있는 스키마 (LLM에게 제공)
DB_SCHEMA = """
SQLite 데이터베이스 스키마:

documents(id, filename, doc_type, project_name, uploaded_at, page_count)

soil_parameters(id, doc_id, borehole_id, layer_name, N_value,
                unit_weight, cohesion, friction_angle, kh, page_number)
  -- 지반 정수: 단위중량(kN/m³), 점착력(kN/m²), 내부마찰각(°), 수평반력계수(kN/m³)

section_checks(id, doc_id, section_id, wall_type, support_type,
               excavation_depth, surcharge_load,
               moment_calc, moment_allow, shear_calc, shear_allow,
               rebar_required, rebar_provided,
               embedment_depth, embedment_SF, embedment_SF_allow,
               head_disp_calc, head_disp_allow,
               max_disp_calc, max_disp_allow,
               overall_result, page_number)
  -- 단면 검토: section_id 예시 'SEC-1O', 'SEC-2A'
  -- overall_result: 'OK' 또는 'NG'

anchor_design(id, doc_id, section_id, stage, free_length, anchor_length,
              design_force, tensile_force, usage_type)
  -- usage_type: 'TEMPORARY' 또는 'PERMANENT'

material_allowables(id, doc_id, material_grade, stress_type,
                    allowable_mpa, condition, page_number)
"""

SQL_SYSTEM_PROMPT = f"""
당신은 엔지니어링 데이터베이스 SQL 전문가입니다.

{DB_SCHEMA}

규칙:
1. SELECT 쿼리만 생성 (INSERT/UPDATE/DELETE 절대 금지)
2. 없는 테이블/컬럼 사용 금지
3. 불가능한 쿼리면 CANNOT_ANSWER 반환
4. SQL 코드만 반환 (설명 없이, 마크다운 없이)
5. 한국어 값 처리 예시: WHERE layer_name = '풍화토-2'
"""


class SQLTool:

    def __init__(self, db_path: str, anthropic_client: AsyncAnthropic) -> None:
        self.db_path = db_path
        self.anthropic = anthropic_client

    async def query(
        self, question: str, doc_ids: list[str] | None = None
    ) -> dict:
        """자연어 질문 → SQL 생성 → 실행 → 결과 반환."""
        # 1. NL → SQL
        sql = await self._generate_sql(question, doc_ids)
        logger.debug(f"생성된 SQL: {sql}")

        if "CANNOT_ANSWER" in sql:
            return {
                "success": False,
                "sql": sql,
                "rows": [],
                "columns": [],
                "error": "SQL 생성 불가",
            }

        # 안전 검증
        if not self._is_safe_sql(sql):
            logger.warning(f"위험한 SQL 차단됨: {sql}")
            return {
                "success": False,
                "sql": sql,
                "rows": [],
                "columns": [],
                "error": "위험한 SQL",
            }

        # 2. 실행
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(sql) as cursor:
                    rows = await cursor.fetchall()
                    columns = (
                        [d[0] for d in cursor.description]
                        if cursor.description
                        else []
                    )
                    row_dicts = [dict(row) for row in rows]

            logger.info(f"SQL 실행 성공: {len(row_dicts)}행 반환")
            return {
                "success": True,
                "sql": sql,
                "rows": row_dicts,
                "columns": columns,
                "row_count": len(row_dicts),
            }
        except Exception as e:
            logger.error(f"SQL 실행 오류: {e}")
            return {
                "success": False,
                "sql": sql,
                "rows": [],
                "columns": [],
                "error": str(e),
            }

    async def _generate_sql(
        self, question: str, doc_ids: list[str] | None
    ) -> str:
        """Claude API로 SQL 생성."""
        doc_filter = ""
        if doc_ids:
            ids = ", ".join(f"'{d}'" for d in doc_ids)
            doc_filter = f"\n추가 조건: doc_id IN ({ids}) 필터 적용"

        response = await self.anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=SQL_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"{question}{doc_filter}",
            }],
        )

        sql = response.content[0].text.strip()
        # 마크다운 제거
        sql = re.sub(r"```sql\s*", "", sql)
        sql = re.sub(r"```\s*", "", sql)
        return sql.strip()

    def _is_safe_sql(self, sql: str) -> bool:
        """위험한 SQL 차단."""
        sql_upper = sql.upper()
        dangerous = [
            "INSERT", "UPDATE", "DELETE", "DROP",
            "CREATE", "ALTER", "TRUNCATE",
        ]
        return not any(keyword in sql_upper for keyword in dangerous)
