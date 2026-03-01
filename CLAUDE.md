# Doaz Engineering RAG — Claude Code Context

## 프로젝트 한 줄 요약
엔지니어링 설계보고서(흙막이, 구조 등) + 기준서(KDS/KCS/ACI/ASME) + 법령을
정확하게 검색/분석하는 **RAG 시스템**. 성능 우선, UI 최소화.

## 기술 스택 (절대 변경 금지)
- **Backend**: Python 3.11, FastAPI
- **LLM**: Anthropic Claude API (claude-sonnet-4-5)
- **Embedding**: `text-embedding-3-large` (OpenAI)
- **Vector DB**: Qdrant (Docker, localhost:6333)
- **Relational DB**: SQLite (개발), PostgreSQL (프로덕션)
- **PDF 파싱**: pdfplumber + PyMuPDF
- **패키지 관리**: uv (pip 대신)

## 디렉토리 구조
```
doaz-eng-rag/
├── CLAUDE.md                  ← 이 파일 (Claude Code 컨텍스트)
├── docs/                      ← 개발 문서 (MD 파일들)
├── src/
│   ├── ingestion/             ← PDF 파싱, 전처리
│   ├── indexing/              ← PageIndex, Vector, SQL 인덱싱
│   ├── retrieval/             ← 쿼리 라우팅, 검색 엔진
│   ├── generation/            ← LLM 프롬프트, 답변 생성
│   └── api/                   ← FastAPI 라우터
├── tests/                     ← 단위 테스트
├── data/
│   ├── uploads/               ← 업로드된 문서 (gitignore)
│   ├── indexes/               ← 생성된 인덱스 파일
│   └── db/                    ← SQLite 파일
├── main.py                    ← 서버 진입점
└── pyproject.toml
```

## 개발 원칙
1. **수치는 반드시 SQL에서** — Vector RAG로 숫자를 찾으면 안 됨
2. **문서 구조 보존** — 테이블/수식을 텍스트로 평탄화 금지
3. **타입 힌트 필수** — 모든 함수에 타입 힌트
4. **에러는 명시적으로** — 조용한 실패 금지, 항상 로깅
5. **비동기 우선** — IO 작업은 async/await

## 빠른 시작
```bash
# 환경 설정
uv sync
docker-compose up -d  # Qdrant 실행

# 서버 시작
uv run python main.py

# 테스트
uv run pytest tests/ -v
```

## 현재 개발 단계
- [ ] Phase 1: PDF 파싱 + SQLite 저장 (1주)
- [ ] Phase 2: Vector 인덱싱 + 기본 검색 (1주)
- [ ] Phase 3: 쿼리 라우팅 + 통합 답변 (1주)
- [ ] Phase 4: PageIndex 계층 트리 (1주)

## 관련 문서
- `docs/00_PROJECT_OVERVIEW.md` — 전체 설계
- `docs/01_ARCHITECTURE.md` — 아키텍처 상세
- `docs/02_SETUP.md` — 환경 설정
- `docs/03_INGESTION.md` — 문서 파싱
- `docs/04_INDEXING.md` — 인덱싱 전략
- `docs/05_RETRIEVAL.md` — 검색 엔진
- `docs/06_API.md` — API 명세
- `docs/07_EVALUATION.md` — 성능 평가
