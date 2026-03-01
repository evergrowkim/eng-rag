# 🚀 Doaz Engineering RAG — 시작 가이드

Claude Code Desktop에서 이 파일을 가장 먼저 읽어야 합니다.

---

## 지금 바로 시작하는 방법 (30분)

### Step 1: 환경 설정 (10분)

```bash
cd doaz-eng-rag

# 패키지 설치
uv sync

# 환경변수 설정
cp .env.example .env
# .env 파일 열어서 API 키 입력:
#   ANTHROPIC_API_KEY=sk-ant-...
#   OPENAI_API_KEY=sk-...

# Qdrant 시작
docker-compose up -d

# DB 초기화
uv run python scripts/init_db.py

# 설치 확인
uv run python scripts/check_setup.py
```

### Step 2: 서버 시작 (1분)

```bash
uv run python main.py

# 출력:
# 🚀 Doaz Engineering RAG 시작
# UI:    http://localhost:8000
# API:   http://localhost:8000/docs
```

### Step 3: 문서 업로드 및 테스트 (10분)

1. 브라우저에서 `http://localhost:8000` 접속
2. PDF 파일 업로드 (흙막이 설계보고서)
3. 테스트 질문 입력:
   - `"풍화토-2의 점착력은?"`
   - `"SEC-1O 근입깊이 안전율은?"`
   - `"앵커 안전율 기준이 뭔가요?"`

### Step 4: 성능 평가 (5분)

```bash
uv run python tests/run_eval.py
```

---

## Claude Code에게 할 수 있는 명령어 예시

```
Phase 1 시작해줘:
"src/ingestion/layout_parser.py 구현해줘. docs/03_INGESTION.md 참고해서"

파싱 테스트:
"data/uploads/sample.pdf 파싱 결과 확인해줘"

SQL 스키마 적용:
"docs/schema.sql로 DB 초기화해줘"

Phase 2 벡터 인덱싱:
"src/indexing/vector_indexer.py 구현해줘. docs/04_INDEXING.md 참고"

API 서버 완성:
"docs/06_API.md 참고해서 main.py와 src/api/ 전체 구현해줘"
```

---

## 개발 문서 지도

```
docs/
├── 00_PROJECT_OVERVIEW.md  ← 전체 목표와 로드맵
├── 01_ARCHITECTURE.md      ← 시스템 설계 (모듈 구조)
├── 02_SETUP.md             ← 환경 설정 (패키지, Docker, DB)
├── 03_INGESTION.md         ← PDF 파싱 구현 (Phase 1)
├── 04_INDEXING.md          ← 벡터 인덱싱 (Phase 2)
├── 05_RETRIEVAL.md         ← 검색 엔진 (Phase 3)
├── 06_API.md               ← FastAPI + 최소 UI (Phase 3)
├── 07_EVALUATION.md        ← 성능 평가 방법
└── schema.sql              ← SQLite 스키마
```

---

## 주의사항

**절대 하지 말 것:**
- Vector RAG로 수치 답변 시도 (반드시 SQL 사용)
- 테이블을 일반 텍스트로 평탄화
- .env 파일 git 커밋

**반드시 할 것:**
- 모든 수치 답변에 단위 포함
- 모든 답변에 출처(페이지) 명시
- 에러 발생 시 로그 확인: `data/logs/app.log`

---

## 막힐 때

```bash
# Qdrant 상태 확인
curl http://localhost:6333/health

# DB 직접 조회
sqlite3 data/db/doaz.db "SELECT * FROM soil_parameters LIMIT 5;"

# API 헬스체크
curl http://localhost:8000/api/v1/health

# 로그 확인
tail -f data/logs/app.log
```
