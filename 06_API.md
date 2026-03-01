# 06. API 서버 + 최소 UI

## 1. FastAPI 라우터 전체 구현

```python
# src/api/documents.py

import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from loguru import logger

from ..ingestion.pipeline import IngestionPipeline
from ..indexing.indexing_pipeline import IndexingPipeline

router = APIRouter(tags=["documents"])

UPLOAD_DIR = Path("data/uploads")
DB_PATH = "data/db/doaz.db"

ingestion = IngestionPipeline(DB_PATH)
indexing = IndexingPipeline()


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    page_count: int
    block_count: int
    table_count: int
    vector_points: int
    status: str


@router.post("/upload", response_model=DocumentInfo)
async def upload_document(file: UploadFile = File(...)):
    """
    PDF 업로드 → 파싱 → 인덱싱.
    전체 과정 자동 실행.
    """
    if not file.filename or not file.filename.endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 업로드 가능합니다")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    save_path = UPLOAD_DIR / file.filename

    # 저장
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    logger.info(f"업로드: {file.filename} ({save_path.stat().st_size // 1024}KB)")

    try:
        # 파싱 + DB 저장
        ingest_result = await ingestion.ingest(str(save_path))
        doc_id = ingest_result["doc_id"]

        # 벡터 인덱싱 (파싱 결과 재사용)
        from ..ingestion.layout_parser import LayoutParser
        parser = LayoutParser()
        parsed_doc = parser.parse(str(save_path))
        index_result = await indexing.index(parsed_doc, doc_id)

        return DocumentInfo(
            doc_id=doc_id,
            filename=file.filename,
            page_count=ingest_result["page_count"],
            block_count=ingest_result["block_count"],
            table_count=ingest_result["table_count"],
            vector_points=index_result["vector_points"],
            status="indexed"
        )

    except Exception as e:
        logger.error(f"처리 실패: {e}")
        raise HTTPException(500, f"문서 처리 실패: {str(e)}")


@router.get("/")
async def list_documents():
    """업로드된 문서 목록."""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, filename, project_name, page_count, uploaded_at FROM documents ORDER BY uploaded_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """문서 및 모든 인덱스 삭제."""
    import aiosqlite
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    # DB 삭제
    async with aiosqlite.connect(DB_PATH) as db:
        for table in ["soil_parameters", "section_checks", "anchor_design",
                      "material_allowables", "chunks"]:
            await db.execute(f"DELETE FROM {table} WHERE doc_id = ?", (doc_id,))
        await db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await db.commit()

    # Vector 삭제
    qdrant = QdrantClient(host="localhost", port=6333)
    qdrant.delete(
        collection_name="doaz_eng_rag",
        points_selector=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        )
    )

    return {"status": "deleted", "doc_id": doc_id}
```

```python
# src/api/query.py

from fastapi import APIRouter
from pydantic import BaseModel
from loguru import logger

from ..retrieval.search_engine import SearchEngine

router = APIRouter(tags=["query"])

engine = SearchEngine(db_path="data/db/doaz.db")


class QueryRequest(BaseModel):
    query: str
    doc_ids: list[str] | None = None


class QueryResponse(BaseModel):
    query: str
    query_type: str
    answer: str
    sources: list[dict]
    sql_query: str | None = None
    vector_count: int = 0


@router.post("/", response_model=QueryResponse)
async def ask(req: QueryRequest):
    """자연어 질문 처리."""
    logger.info(f"질문: {req.query}")

    result = await engine.search(req.query, req.doc_ids)

    return QueryResponse(
        query=req.query,
        query_type=result.query_type,
        answer=result.answer,
        sources=result.sources,
        sql_query=result.sql_result.get("sql") if result.sql_result else None,
        vector_count=len(result.vector_results)
    )


@router.post("/sql")
async def direct_sql(body: dict):
    """직접 SQL 실행 (개발/디버깅용)."""
    sql = body.get("sql", "")
    import aiosqlite
    async with aiosqlite.connect("data/db/doaz.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql) as cursor:
            rows = await cursor.fetchall()
    return {"rows": [dict(r) for r in rows]}
```

```python
# src/api/health.py

from fastapi import APIRouter
from qdrant_client import QdrantClient
import aiosqlite

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    status = {"status": "ok"}

    # Qdrant 확인
    try:
        qdrant = QdrantClient(host="localhost", port=6333)
        qdrant.get_collections()
        status["qdrant"] = "connected"
    except Exception:
        status["qdrant"] = "error"
        status["status"] = "degraded"

    # SQLite 확인
    try:
        async with aiosqlite.connect("data/db/doaz.db") as db:
            await db.execute("SELECT 1")
        status["db"] = "connected"
    except Exception:
        status["db"] = "error"
        status["status"] = "degraded"

    return status
```

---

## 2. 최소 UI (단일 HTML 파일)

성능 테스트에 필요한 최소한의 인터페이스.
파일 업로드 + 채팅 인터페이스만 구현.

```python
# src/api/ui.py

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

MINIMAL_UI = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Doaz Engineering RAG</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #f5f5f5; color: #333; }

  .container { max-width: 900px; margin: 0 auto; padding: 20px; }

  h1 { font-size: 20px; font-weight: 600; color: #1a1a2e; margin-bottom: 20px;
       border-bottom: 2px solid #4a90e2; padding-bottom: 10px; }

  /* 업로드 영역 */
  .upload-zone { background: white; border: 2px dashed #ccc; border-radius: 8px;
                 padding: 20px; margin-bottom: 20px; text-align: center;
                 cursor: pointer; transition: border-color .2s; }
  .upload-zone:hover { border-color: #4a90e2; }
  .upload-zone input { display: none; }
  .upload-zone label { cursor: pointer; color: #4a90e2; font-weight: 500; }

  /* 문서 목록 */
  .doc-list { background: white; border-radius: 8px; padding: 16px;
              margin-bottom: 20px; }
  .doc-list h2 { font-size: 14px; color: #666; margin-bottom: 10px; }
  .doc-item { display: flex; align-items: center; padding: 8px;
              border-bottom: 1px solid #f0f0f0; font-size: 13px; }
  .doc-item:last-child { border-bottom: none; }
  .doc-name { flex: 1; font-weight: 500; }
  .doc-meta { color: #999; font-size: 12px; margin-right: 12px; }
  .doc-delete { color: #e74c3c; cursor: pointer; border: none;
                background: none; font-size: 12px; }
  .doc-checkbox { margin-right: 8px; }

  /* 채팅 영역 */
  .chat-container { background: white; border-radius: 8px; overflow: hidden; }
  .chat-messages { height: 400px; overflow-y: auto; padding: 16px; }
  .message { margin-bottom: 16px; }
  .message.user { text-align: right; }
  .message.assistant { text-align: left; }
  .bubble { display: inline-block; padding: 10px 14px; border-radius: 12px;
            max-width: 80%; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
  .user .bubble { background: #4a90e2; color: white; }
  .assistant .bubble { background: #f0f0f0; color: #333; }
  .meta { font-size: 11px; color: #aaa; margin-top: 4px; }
  .source-tag { display: inline-block; background: #e8f0fe; color: #4a90e2;
                padding: 2px 8px; border-radius: 4px; font-size: 11px;
                margin: 2px; }
  .sql-box { background: #1e1e1e; color: #d4d4d4; padding: 8px 12px;
             border-radius: 6px; font-size: 11px; font-family: monospace;
             margin-top: 6px; white-space: pre-wrap; }

  .chat-input { display: flex; padding: 12px; border-top: 1px solid #eee; }
  .chat-input input { flex: 1; padding: 10px; border: 1px solid #ddd;
                      border-radius: 6px; font-size: 14px; outline: none; }
  .chat-input input:focus { border-color: #4a90e2; }
  .chat-input button { margin-left: 8px; padding: 10px 20px; background: #4a90e2;
                       color: white; border: none; border-radius: 6px;
                       cursor: pointer; font-size: 14px; }
  .chat-input button:disabled { background: #ccc; cursor: not-allowed; }

  /* 상태 표시 */
  .status { padding: 8px 12px; border-radius: 6px; font-size: 13px; margin: 8px 0; }
  .status.success { background: #d4edda; color: #155724; }
  .status.error { background: #f8d7da; color: #721c24; }
  .status.loading { background: #d1ecf1; color: #0c5460; }

  .query-type-badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
                      font-size: 11px; font-weight: 600;
                      background: #fff3cd; color: #856404; }
</style>
</head>
<body>
<div class="container">
  <h1>⚙️ Doaz Engineering RAG</h1>

  <!-- 업로드 -->
  <div class="upload-zone" id="uploadZone">
    <input type="file" id="fileInput" accept=".pdf" multiple>
    <label for="fileInput">📄 PDF 파일을 클릭하여 업로드</label>
    <p style="color:#999;font-size:12px;margin-top:6px;">
      설계보고서, 기준서, 지반조사보고서 지원
    </p>
  </div>
  <div id="uploadStatus"></div>

  <!-- 문서 목록 -->
  <div class="doc-list">
    <h2>업로드된 문서 <span id="docCount" style="color:#4a90e2"></span></h2>
    <div id="docList"></div>
  </div>

  <!-- 채팅 -->
  <div class="chat-container">
    <div class="chat-messages" id="chatMessages">
      <div class="message assistant">
        <div class="bubble">안녕하세요! 엔지니어링 문서에 관한 질문을 입력하세요.

예시 질문:
• "풍화토-2의 점착력은?"
• "SEC-1O 단면의 근입깊이 안전율은?"
• "굴착깊이가 가장 깊은 단면은?"
• "앵커 안전율 기준이 뭔가요?"</div>
      </div>
    </div>
    <div class="chat-input">
      <input type="text" id="queryInput" placeholder="질문을 입력하세요..." />
      <button id="sendBtn" onclick="sendQuery()">전송</button>
    </div>
  </div>
</div>

<script>
const API = 'http://localhost:8000/api/v1';
let selectedDocIds = [];

// 파일 업로드
document.getElementById('fileInput').addEventListener('change', async (e) => {
  const files = Array.from(e.target.files);
  for (const file of files) {
    await uploadFile(file);
  }
  loadDocuments();
});

async function uploadFile(file) {
  const statusEl = document.getElementById('uploadStatus');
  statusEl.innerHTML = `<div class="status loading">📤 "${file.name}" 업로드 및 인덱싱 중...</div>`;

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch(`${API}/documents/upload`, { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);

    statusEl.innerHTML = `<div class="status success">
      ✅ "${file.name}" 완료 — 
      ${data.page_count}페이지, ${data.block_count}블록, 
      벡터 ${data.vector_points}개
    </div>`;
  } catch (err) {
    statusEl.innerHTML = `<div class="status error">❌ 실패: ${err.message}</div>`;
  }
}

async function loadDocuments() {
  const res = await fetch(`${API}/documents`);
  const docs = await res.json();

  document.getElementById('docCount').textContent = `(${docs.length}개)`;

  const listEl = document.getElementById('docList');
  if (!docs.length) {
    listEl.innerHTML = '<div style="color:#aaa;font-size:13px">업로드된 문서 없음</div>';
    return;
  }

  listEl.innerHTML = docs.map(d => `
    <div class="doc-item">
      <input type="checkbox" class="doc-checkbox" value="${d.id}"
             onchange="toggleDoc(this)" checked>
      <span class="doc-name">${d.filename}</span>
      <span class="doc-meta">${d.project_name || '-'} | ${d.page_count}p</span>
      <button class="doc-delete" onclick="deleteDoc('${d.id}', this)">삭제</button>
    </div>
  `).join('');

  // 전체 선택
  selectedDocIds = docs.map(d => d.id);
}

function toggleDoc(checkbox) {
  if (checkbox.checked) {
    selectedDocIds.push(checkbox.value);
  } else {
    selectedDocIds = selectedDocIds.filter(id => id !== checkbox.value);
  }
}

async function deleteDoc(docId, btn) {
  if (!confirm('삭제하시겠습니까?')) return;
  await fetch(`${API}/documents/${docId}`, { method: 'DELETE' });
  loadDocuments();
}

// 채팅
document.getElementById('queryInput').addEventListener('keypress', (e) => {
  if (e.key === 'Enter') sendQuery();
});

async function sendQuery() {
  const input = document.getElementById('queryInput');
  const query = input.value.trim();
  if (!query) return;

  input.value = '';
  document.getElementById('sendBtn').disabled = true;

  appendMessage('user', query);

  try {
    const res = await fetch(`${API}/query/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        doc_ids: selectedDocIds.length ? selectedDocIds : null
      })
    });
    const data = await res.json();

    // 답변 조합
    let content = data.answer;

    // 메타 정보
    let meta = `<div class="meta">
      <span class="query-type-badge">${data.query_type}</span>
      ` + (data.sql_query ? `
      <div class="sql-box">${escHtml(data.sql_query)}</div>` : '') + `
      ` + (data.sources || []).filter(s => s.type === 'document').map(s =>
        `<span class="source-tag">📄 ${s.filename} p.${s.page} (${s.score})</span>`
      ).join('') + `
    </div>`;

    appendMessage('assistant', content, meta);

  } catch (err) {
    appendMessage('assistant', `오류: ${err.message}`);
  } finally {
    document.getElementById('sendBtn').disabled = false;
  }
}

function appendMessage(role, text, metaHtml = '') {
  const el = document.createElement('div');
  el.className = `message ${role}`;
  el.innerHTML = `<div class="bubble">${escHtml(text)}</div>${metaHtml}`;
  const container = document.getElementById('chatMessages');
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}

function escHtml(str) {
  return (str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// 초기 로드
loadDocuments();
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def ui():
    return MINIMAL_UI
```

---

## 3. main.py 완성본

```python
# main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import uvicorn
import sys

from src.api import documents, query, health, ui

# 로그 설정
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")
logger.add("data/logs/app.log", rotation="10 MB", retention="7 days")

app = FastAPI(
    title="Doaz Engineering RAG",
    description="엔지니어링 문서 특화 RAG",
    version="0.1.0",
    docs_url="/docs"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(ui.router)
app.include_router(health.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1/documents")
app.include_router(query.router, prefix="/api/v1/query")

if __name__ == "__main__":
    logger.info("🚀 Doaz Engineering RAG 시작")
    logger.info("  UI:    http://localhost:8000")
    logger.info("  API:   http://localhost:8000/docs")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="warning")
```

---

## 4. 시작 순서

```bash
# 1. Docker (Qdrant)
docker-compose up -d

# 2. DB 초기화 (처음 한 번)
uv run python scripts/init_db.py

# 3. 서버 시작
uv run python main.py

# 4. 브라우저 접속
open http://localhost:8000

# 5. API 문서
open http://localhost:8000/docs
```

---

## 5. 빠른 테스트 (curl)

```bash
# 문서 업로드
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -F "file=@data/uploads/sample.pdf"

# 질문
curl -X POST http://localhost:8000/api/v1/query/ \
  -H "Content-Type: application/json" \
  -d '{"query": "풍화토-2의 점착력은?"}' | python -m json.tool

# 직접 SQL
curl -X POST http://localhost:8000/api/v1/query/sql \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM soil_parameters WHERE layer_name LIKE '\''%풍화토%'\''"}' | python -m json.tool
```
