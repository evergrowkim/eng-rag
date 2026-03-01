"""Doaz Engineering RAG — 서버 진입점."""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv(override=True)  # .env 파일에서 환경변수 로드

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.api import documents, health, query, ui

# 로그 설정
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")
logger.add("data/logs/app.log", rotation="10 MB", retention="7 days")

app = FastAPI(
    title="Doaz Engineering RAG",
    description="엔지니어링 문서 특화 RAG",
    version="0.1.0",
    docs_url="/docs",
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
    logger.info("Doaz Engineering RAG 시작")
    logger.info("  UI:    http://localhost:8000")
    logger.info("  API:   http://localhost:8000/docs")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="warning")
