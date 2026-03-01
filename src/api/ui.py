"""최소 UI — 파일 업로드 + 채팅 인터페이스."""

from __future__ import annotations

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
  <h1>Doaz Engineering RAG</h1>

  <!-- 업로드 -->
  <div class="upload-zone" id="uploadZone">
    <input type="file" id="fileInput" accept=".pdf" multiple>
    <label for="fileInput">PDF 파일을 클릭하여 업로드</label>
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
        <div class="bubble">엔지니어링 문서에 관한 질문을 입력하세요.

예시 질문:
- "풍화토-2의 점착력은?"
- "SEC-1O 단면의 근입깊이 안전율은?"
- "굴착깊이가 가장 깊은 단면은?"
- "앵커 안전율 기준이 뭔가요?"</div>
      </div>
    </div>
    <div class="chat-input">
      <input type="text" id="queryInput" placeholder="질문을 입력하세요..." />
      <button id="sendBtn" onclick="sendQuery()">전송</button>
    </div>
  </div>
</div>

<script>
const API = window.location.origin + '/api/v1';
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
  statusEl.innerHTML = '<div class="status loading">"' + escHtml(file.name) + '" 업로드 및 인덱싱 중...</div>';

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch(API + '/documents/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);

    statusEl.innerHTML = '<div class="status success">"' + escHtml(file.name) + '" 완료 — '
      + data.page_count + '페이지, ' + data.block_count + '블록, '
      + '벡터 ' + data.vector_points + '개</div>';
  } catch (err) {
    statusEl.innerHTML = '<div class="status error">실패: ' + escHtml(err.message) + '</div>';
  }
}

async function loadDocuments() {
  const res = await fetch(API + '/documents');
  const docs = await res.json();

  document.getElementById('docCount').textContent = '(' + docs.length + '개)';

  const listEl = document.getElementById('docList');
  if (!docs.length) {
    listEl.innerHTML = '<div style="color:#aaa;font-size:13px">업로드된 문서 없음</div>';
    return;
  }

  listEl.innerHTML = docs.map(function(d) {
    return '<div class="doc-item">'
      + '<input type="checkbox" class="doc-checkbox" value="' + d.id + '" onchange="toggleDoc(this)" checked>'
      + '<span class="doc-name">' + escHtml(d.filename) + '</span>'
      + '<span class="doc-meta">' + escHtml(d.project_name || '-') + ' | ' + d.page_count + 'p</span>'
      + '<button class="doc-delete" onclick="deleteDoc(\'' + d.id + '\', this)">삭제</button>'
      + '</div>';
  }).join('');

  // 전체 선택
  selectedDocIds = docs.map(function(d) { return d.id; });
}

function toggleDoc(checkbox) {
  if (checkbox.checked) {
    selectedDocIds.push(checkbox.value);
  } else {
    selectedDocIds = selectedDocIds.filter(function(id) { return id !== checkbox.value; });
  }
}

async function deleteDoc(docId, btn) {
  if (!confirm('삭제하시겠습니까?')) return;
  await fetch(API + '/documents/' + docId, { method: 'DELETE' });
  loadDocuments();
}

// 채팅
document.getElementById('queryInput').addEventListener('keypress', function(e) {
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
    const res = await fetch(API + '/query/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: query,
        doc_ids: selectedDocIds.length ? selectedDocIds : null
      })
    });
    const data = await res.json();

    // 답변 조합
    var content = data.answer;

    // 메타 정보
    var meta = '<div class="meta">'
      + '<span class="query-type-badge">' + escHtml(data.query_type) + '</span>'
      + (data.sql_query ? '<div class="sql-box">' + escHtml(data.sql_query) + '</div>' : '')
      + (data.sources || []).filter(function(s) { return s.type === 'document'; }).map(function(s) {
          return '<span class="source-tag">' + escHtml(s.filename) + ' p.' + s.page + ' (' + s.score + ')</span>';
        }).join('')
      + '</div>';

    appendMessage('assistant', content, meta);

  } catch (err) {
    appendMessage('assistant', '오류: ' + err.message);
  } finally {
    document.getElementById('sendBtn').disabled = false;
  }
}

function appendMessage(role, text, metaHtml) {
  metaHtml = metaHtml || '';
  var el = document.createElement('div');
  el.className = 'message ' + role;
  el.innerHTML = '<div class="bubble">' + escHtml(text) + '</div>' + metaHtml;
  var container = document.getElementById('chatMessages');
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
async def ui() -> HTMLResponse:
    return HTMLResponse(content=MINIMAL_UI)
