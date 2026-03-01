# 01. 시스템 아키텍처

## 1. 전체 데이터 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│  INGESTION                                                       │
│                                                                  │
│  PDF Upload → LayoutParser → BlockClassifier → MetaExtractor    │
│                    ↓               ↓                ↓           │
│               [텍스트블록]   [테이블블록]      [메타데이터]       │
└──────────────────┬──────────────┬────────────────┬─────────────┘
                   ↓              ↓                ↓
┌─────────────────────────────────────────────────────────────────┐
│  INDEX LAYER                                                     │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  Vector Index   │  │   SQL Database   │  │  Page Index   │  │
│  │  (Qdrant)       │  │   (SQLite)       │  │  (JSON Tree)  │  │
│  │                 │  │                  │  │               │  │
│  │ • 섹션 임베딩    │  │ • 지반정수        │  │ • 문서 계층   │  │
│  │ • 청크 메타데이터│  │ • 단면검토 결과   │  │ • 섹션 요약   │  │
│  │ • 기준서 조항   │  │ • 앵커 설계값    │  │ • 페이지 참조 │  │
│  └─────────────────┘  └──────────────────┘  └───────────────┘  │
└──────────────────┬──────────────┬────────────────┬─────────────┘
                   ↓              ↓                ↓
┌─────────────────────────────────────────────────────────────────┐
│  QUERY ENGINE                                                    │
│                                                                  │
│  사용자 질문 → QueryClassifier → RoutingPlan                    │
│                                       ↓                         │
│              ┌────────────────────────┤                         │
│              ↓            ↓           ↓                         │
│         VectorSearch   SQLQuery   TreeSearch                     │
│              └────────────────────────┘                         │
│                           ↓                                     │
│                    ContextAssembler                              │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  GENERATION                                                      │
│                                                                  │
│  EngineeringPrompt → Claude API → ResponseFormatter             │
│  (출처 명시, 단위 포함, 판정 근거 포함)                           │
└─────────────────────────────────────────────────────────────────┘
```

## 2. 모듈 상세 설계

### 2-1. LayoutParser

```python
# src/ingestion/layout_parser.py

class LayoutParser:
    """
    PDF에서 레이아웃 구조를 보존하며 블록 단위로 추출.
    pdfplumber(텍스트/테이블) + PyMuPDF(이미지) 조합.
    """
    
    def parse(self, pdf_path: str) -> ParsedDocument:
        """
        Returns:
            ParsedDocument:
                - pages: List[PageContent]
                - metadata: DocumentMetadata
        """
        pass
    
    def _extract_tables(self, page) -> List[TableBlock]:
        """
        테이블을 Dict[str, Any] 형태로 추출.
        행/열 헤더 보존 필수.
        """
        pass
    
    def _classify_block(self, block: RawBlock) -> BlockType:
        """
        TEXT / TABLE / EQUATION / FIGURE / CHECK_RESULT
        CHECK_RESULT: "179.83 < 270.32 O.K" 패턴
        """
        pass
```

### 2-2. BlockClassifier

```python
# src/ingestion/block_classifier.py

from enum import Enum

class BlockType(Enum):
    TEXT = "text"
    TABLE = "table"
    EQUATION = "equation"
    FIGURE = "figure"
    CHECK_RESULT = "check_result"   # 검토결과 (값 < 허용값 O.K)
    SUNEX_OUTPUT = "sunex_output"   # 전산해석 출력
    SOIL_TABLE = "soil_table"       # 지반정수 테이블 (특수 처리)

class BlockClassifier:
    """
    추출된 블록의 유형을 판별.
    유형별로 다른 저장 파이프라인 적용.
    
    판별 규칙:
    - CHECK_RESULT: r"[\d.]+ [<>] [\d.]+ O\.?K" 패턴
    - SOIL_TABLE: 헤더에 N치, 단위중량, 점착력, 내부마찰각 포함
    - SUNEX_OUTPUT: "SUNEX Ver", "Step No." 텍스트 포함
    """
    
    # 정규식 패턴 모음
    CHECK_RESULT_PATTERN = r"(\d+\.?\d*)\s*[<>]\s*(\d+\.?\d*)\s*(O\.?K|N\.?G)"
    SOIL_PARAM_HEADERS = ["N치", "단위중량", "점착력", "내부마찰각"]
```

### 2-3. SQL 스키마

```sql
-- data/db/schema.sql

-- 문서 메타데이터
CREATE TABLE documents (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    doc_type        TEXT NOT NULL,   -- DESIGN_REPORT / STANDARD / REGULATION
    project_name    TEXT,
    uploaded_at     TEXT DEFAULT (datetime('now')),
    page_count      INTEGER,
    file_size       INTEGER
);

-- 지반정수
CREATE TABLE soil_parameters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT REFERENCES documents(id),
    borehole_id     TEXT,            -- BH-9, NBH-14
    layer_name      TEXT,
    N_value         INTEGER,
    unit_weight     REAL,            -- kN/m³
    cohesion        REAL,            -- kN/m²
    friction_angle  REAL,            -- °
    kh              REAL,            -- kN/m³ 수평지반반력계수
    page_number     INTEGER
);

-- 단면 검토 결과
CREATE TABLE section_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT REFERENCES documents(id),
    section_id      TEXT,            -- SEC-1O, SEC-2A
    wall_type       TEXT,            -- CIP, H-PILE+토류판
    support_type    TEXT,            -- Earth Anchor, Strut
    excavation_depth REAL,           -- m
    surcharge_load  REAL,            -- kN/m²
    
    -- CIP / 엄지말뚝 검토
    moment_calc     REAL,
    moment_allow    REAL,
    shear_calc      REAL,
    shear_allow     REAL,
    rebar_required  REAL,            -- mm²
    rebar_provided  REAL,            -- mm²
    
    -- 근입깊이
    embedment_depth REAL,
    embedment_SF    REAL,
    embedment_SF_allow REAL,
    
    -- 변위
    head_disp_calc  REAL,            -- mm
    head_disp_allow REAL,            -- mm
    max_disp_calc   REAL,            -- mm
    max_disp_allow  REAL,            -- mm
    
    -- 판정
    overall_result  TEXT,            -- OK / NG
    page_number     INTEGER
);

-- 앵커 설계
CREATE TABLE anchor_design (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT REFERENCES documents(id),
    section_id      TEXT,
    stage           INTEGER,         -- 1, 2, 3, 4단
    free_length     REAL,            -- m
    anchor_length   REAL,            -- m
    design_force    REAL,            -- kN
    tensile_force   REAL,            -- kN
    usage_type      TEXT             -- TEMPORARY / PERMANENT
);

-- 재료 허용응력
CREATE TABLE material_allowables (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT REFERENCES documents(id),
    material_grade  TEXT,            -- SS275, SM355
    stress_type     TEXT,            -- 휨인장, 압축, 전단
    allowable_mpa   REAL,
    condition       TEXT,            -- 가설재 할증 등
    page_number     INTEGER
);

-- 청크 저장 (Vector DB와 연동)
CREATE TABLE chunks (
    id              TEXT PRIMARY KEY,  -- UUID
    doc_id          TEXT REFERENCES documents(id),
    block_type      TEXT,
    content         TEXT NOT NULL,
    page_number     INTEGER,
    section_path    TEXT,             -- "03.설계기준/3-2.지반정수"
    table_data      TEXT,             -- JSON (테이블인 경우)
    qdrant_id       TEXT              -- Qdrant 포인트 ID
);
```

### 2-4. QueryClassifier

```python
# src/retrieval/query_classifier.py

from enum import Enum
from dataclasses import dataclass
from typing import List

class QueryType(Enum):
    NUMERICAL = "numerical"       # 수치 조회 → SQL 우선
    CONCEPTUAL = "conceptual"     # 개념/정책 → Vector 우선
    COMPLIANCE = "compliance"     # 기준 적합성 → SQL + Vector
    COMPARATIVE = "comparative"   # 비교 분석 → SQL 집계
    MULTI_HOP = "multi_hop"       # 복합 추론 → 모든 도구

@dataclass
class RoutingPlan:
    query_type: QueryType
    use_sql: bool
    use_vector: bool
    use_pageindex: bool
    parallel: bool                # 병렬 실행 여부
    sql_hint: str | None = None   # SQL 쿼리 힌트

class QueryClassifier:
    """
    질문 유형 분류 규칙:
    
    NUMERICAL 신호:
      - 숫자 포함 ("몇 m", "얼마", "kN")
      - 비교 표현 ("초과", "이하", "만족")
      - 집계 표현 ("최대", "최소", "평균", "모두")
    
    CONCEPTUAL 신호:
      - "방법", "이유", "어떻게", "설명"
      - 기준서 조항 참조 ("KDS", "ACI", "조항")
    
    COMPLIANCE 신호:
      - "만족하는가", "적합한가", "위반"
      - "현행 기준", "최신"
    
    COMPARATIVE 신호:
      - "비교", "차이", "vs", "대비"
      - 복수의 단면/문서 언급
    """
    
    NUMERICAL_PATTERNS = [
        r"\d+\s*(m|mm|kN|MPa|°)",
        "몇", "얼마", "값", "수치", "결과",
        "최대", "최소", "평균", "합계", "총"
    ]
    
    COMPLIANCE_PATTERNS = [
        "만족", "충족", "적합", "준수",
        "위반", "초과", "미달", "검토"
    ]
    
    def classify(self, query: str) -> RoutingPlan:
        ...
```

### 2-5. 엔지니어링 프롬프트 템플릿

```python
# src/generation/prompts.py

ENGINEERING_SYSTEM_PROMPT = """
당신은 엔지니어링 설계 전문 AI 어시스턴트입니다.

응답 규칙:
1. 수치는 반드시 단위와 함께 표시 (예: 179.83 kN·m, 2.255, 13 kN/m²)
2. 모든 수치에 출처 명시 (문서명, 페이지, 섹션번호)
3. 기준값과 계산값을 구분하여 표시
4. 판정 결과는 명확하게 (O.K / N.G)
5. 불확실한 경우 "확인 필요"로 표시, 추측 금지

출처 표시 형식:
[출처: {문서명} p.{페이지} / {섹션번호}]

예시 응답:
"SEC-1O 단면의 CIP 발생 휨모멘트는 179.83 kN·m이며,
허용 휨모멘트 270.32 kN·m에 대해 안전율 여유가 충분합니다.
[출처: 전주기자촌 흙막이 가시설 설계보고서 p.4-18 / 4-2-17]"
"""

NUMERICAL_QUERY_PROMPT = """
다음은 데이터베이스에서 조회한 수치 데이터입니다:

{sql_results}

그리고 관련 문서 내용입니다:

{vector_context}

위 데이터를 바탕으로 다음 질문에 정확하게 답하세요:
{query}

반드시 수치의 출처와 단위를 명시하세요.
"""
```

## 3. API 엔드포인트 설계

```
POST /api/v1/documents/upload
  - 파일 업로드 + 파싱 + 인덱싱 실행
  - Response: { doc_id, status, chunk_count, table_count }

GET  /api/v1/documents
  - 업로드된 문서 목록
  
DELETE /api/v1/documents/{doc_id}
  - 문서 및 모든 인덱스 삭제

POST /api/v1/query
  - 자연어 질문
  - Body: { query, doc_ids?: [] }
  - Response: { answer, sources, query_type, sql_used }

GET  /api/v1/query/sql
  - 직접 SQL 실행 (개발/디버깅용)
  - Body: { sql }

GET  /api/v1/health
  - 시스템 상태 확인
```

## 4. 데이터 모델 (Pydantic)

```python
# src/models.py

from pydantic import BaseModel
from typing import Optional, List, Any
from enum import Enum

class DocumentType(str, Enum):
    DESIGN_REPORT = "design_report"
    STANDARD = "standard"
    REGULATION = "regulation"

class BlockType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    CHECK_RESULT = "check_result"
    SOIL_TABLE = "soil_table"

class ParsedBlock(BaseModel):
    block_type: BlockType
    content: str
    page: int
    section_path: str
    table_data: Optional[List[dict]] = None
    check_values: Optional[dict] = None  # {calc, allow, result}

class DocumentMetadata(BaseModel):
    project_name: Optional[str]
    doc_type: DocumentType
    referenced_standards: List[str] = []
    page_count: int
    
class QueryResponse(BaseModel):
    answer: str
    sources: List[dict]           # [{doc, page, section}]
    query_type: str
    sql_query: Optional[str]      # 사용된 SQL (투명성)
    confidence: float             # 0~1
```

## 5. 에러 처리 전략

```python
# 모든 에러는 명시적으로 로깅하고 사용자에게 알림

class IngestionError(Exception):
    """PDF 파싱 실패"""
    pass

class IndexingError(Exception):
    """인덱싱 실패"""
    pass

class RetrievalError(Exception):
    """검색 실패"""
    pass

# FastAPI 예외 핸들러
@app.exception_handler(IngestionError)
async def ingestion_error_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"error": "문서 파싱 실패", "detail": str(exc)}
    )
```
