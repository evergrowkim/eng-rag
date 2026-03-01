# 02. 개발 환경 설정

## 1. 사전 요구사항

```
Python 3.11+
Docker Desktop (Qdrant 실행용)
uv (Python 패키지 관리자)
git
```

## 2. uv 설치 (처음 한 번만)

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 설치 확인
uv --version
```

## 3. 프로젝트 초기화

```bash
# 저장소 클론 (또는 디렉토리 생성)
mkdir doaz-eng-rag && cd doaz-eng-rag

# Python 3.11 환경 생성
uv init --python 3.11

# pyproject.toml 의존성 추가
uv add fastapi uvicorn pdfplumber pymupdf \
       qdrant-client openai anthropic \
       sqlalchemy aiosqlite pydantic \
       python-multipart loguru pytest \
       httpx pytest-asyncio

uv add --dev ruff mypy
```

## 4. pyproject.toml 전체 내용

```toml
[project]
name = "doaz-eng-rag"
version = "0.1.0"
description = "Doaz Engineering RAG System"
requires-python = ">=3.11"

dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pdfplumber>=0.11.0",
    "pymupdf>=1.24.0",
    "qdrant-client>=1.12.0",
    "openai>=1.57.0",
    "anthropic>=0.40.0",
    "sqlalchemy>=2.0.0",
    "aiosqlite>=0.20.0",
    "pydantic>=2.10.0",
    "python-multipart>=0.0.18",
    "loguru>=0.7.0",
    "pytest>=8.0.0",
    "httpx>=0.27.0",
    "pytest-asyncio>=0.24.0",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = true
```

## 5. 환경 변수 (.env)

```bash
# .env 파일 생성
cat > .env << 'EOF'
# LLM
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...       # 임베딩용

# Vector DB
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION=doaz_eng_rag

# Database
DATABASE_URL=sqlite+aiosqlite:///./data/db/doaz.db

# 파일 저장
UPLOAD_DIR=./data/uploads
INDEX_DIR=./data/indexes

# 개발 설정
DEBUG=true
LOG_LEVEL=DEBUG
EOF

# .gitignore에 추가
echo ".env" >> .gitignore
echo "data/uploads/" >> .gitignore
echo "data/db/" >> .gitignore
```

## 6. Qdrant 실행 (Docker)

```bash
# docker-compose.yml 생성
cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ./data/qdrant:/qdrant/storage
    environment:
      - QDRANT__SERVICE__HTTP_PORT=6333
    restart: unless-stopped
EOF

# Qdrant 시작
docker-compose up -d

# 상태 확인
curl http://localhost:6333/health
# → {"title":"qdrant - vector search engine","version":"..."}
```

## 7. 디렉토리 구조 생성

```bash
mkdir -p src/{ingestion,indexing,retrieval,generation,api}
mkdir -p data/{uploads,indexes,db}
mkdir -p tests

# __init__.py 생성
touch src/__init__.py
touch src/ingestion/__init__.py
touch src/indexing/__init__.py
touch src/retrieval/__init__.py
touch src/generation/__init__.py
touch src/api/__init__.py
touch tests/__init__.py
```

## 8. 데이터베이스 초기화

```bash
# DB 디렉토리 생성
mkdir -p data/db

# 스키마 적용 스크립트
cat > scripts/init_db.py << 'EOF'
import asyncio
import aiosqlite
from pathlib import Path

SCHEMA = Path("docs/schema.sql").read_text()

async def init():
    async with aiosqlite.connect("data/db/doaz.db") as db:
        await db.executescript(SCHEMA)
        await db.commit()
    print("✅ Database initialized")

asyncio.run(init())
EOF

uv run python scripts/init_db.py
```

## 9. 설치 확인

```bash
# 전체 확인 스크립트
cat > scripts/check_setup.py << 'EOF'
import sys
print(f"Python: {sys.version}")

# 패키지 확인
try:
    import pdfplumber; print("✅ pdfplumber")
    import fitz; print("✅ PyMuPDF")
    import qdrant_client; print("✅ qdrant-client")
    import anthropic; print("✅ anthropic")
    import openai; print("✅ openai")
    import fastapi; print("✅ fastapi")
except ImportError as e:
    print(f"❌ {e}")

# Qdrant 연결 확인
from qdrant_client import QdrantClient
try:
    client = QdrantClient(host="localhost", port=6333)
    client.get_collections()
    print("✅ Qdrant 연결 성공")
except Exception as e:
    print(f"❌ Qdrant 연결 실패: {e}")

# 환경변수 확인
import os
from dotenv import load_dotenv
load_dotenv()
for key in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]:
    val = os.getenv(key)
    print(f"{'✅' if val else '❌'} {key}: {'설정됨' if val else '미설정'}")

print("\n모든 확인 완료!")
EOF

uv run python scripts/check_setup.py
```

## 10. main.py (서버 진입점)

```python
# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import uvicorn

from src.api import documents, query, health

app = FastAPI(
    title="Doaz Engineering RAG",
    description="엔지니어링 문서 특화 RAG 시스템",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1/documents")
app.include_router(query.router, prefix="/api/v1/query")

if __name__ == "__main__":
    logger.info("🚀 Doaz Engineering RAG 서버 시작")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
```

```bash
# 서버 실행
uv run python main.py

# 확인
curl http://localhost:8000/api/v1/health
# → {"status":"ok","qdrant":"connected","db":"connected"}
```

## 11. Claude Code Desktop 설정

Claude Code에서 이 프로젝트를 열 때:

```bash
# 프로젝트 루트에서 Claude Code 실행
claude

# Claude Code가 자동으로 CLAUDE.md를 읽고 컨텍스트 파악
# 개발 시작 명령어 예시:
# "Phase 1 시작: PDF 파싱 모듈 구현해줘"
# "src/ingestion/layout_parser.py 구현해줘"
```

### MCP 서버 설정 (선택사항)
```json
// claude_desktop_config.json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/doaz-eng-rag"]
    }
  }
}
```
