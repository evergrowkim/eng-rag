-- Doaz Engineering RAG - SQLite Schema
-- 파일: docs/schema.sql

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 문서 메타데이터
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    doc_type        TEXT NOT NULL DEFAULT 'design_report',
    project_name    TEXT,
    uploaded_at     TEXT DEFAULT (datetime('now')),
    page_count      INTEGER,
    file_size       INTEGER
);

-- 지반정수
CREATE TABLE IF NOT EXISTS soil_parameters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    borehole_id     TEXT,
    layer_name      TEXT NOT NULL,
    N_value         REAL,
    unit_weight     REAL,
    cohesion        REAL,
    friction_angle  REAL,
    kh              REAL,
    page_number     INTEGER
);

-- 단면 검토 결과
CREATE TABLE IF NOT EXISTS section_checks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id              TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id          TEXT,
    wall_type           TEXT,
    support_type        TEXT,
    excavation_depth    REAL,
    surcharge_load      REAL,
    moment_calc         REAL,
    moment_allow        REAL,
    shear_calc          REAL,
    shear_allow         REAL,
    rebar_required      REAL,
    rebar_provided      REAL,
    embedment_depth     REAL,
    embedment_SF        REAL,
    embedment_SF_allow  REAL DEFAULT 1.20,
    head_disp_calc      REAL,
    head_disp_allow     REAL,
    max_disp_calc       REAL,
    max_disp_allow      REAL,
    overall_result      TEXT,
    page_number         INTEGER
);

-- 앵커 설계
CREATE TABLE IF NOT EXISTS anchor_design (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id      TEXT,
    stage           INTEGER,
    free_length     REAL,
    anchor_length   REAL,
    design_force    REAL,
    tensile_force   REAL,
    usage_type      TEXT DEFAULT 'TEMPORARY'
);

-- 재료 허용응력
CREATE TABLE IF NOT EXISTS material_allowables (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    material_grade  TEXT,
    stress_type     TEXT,
    allowable_mpa   REAL,
    condition       TEXT,
    page_number     INTEGER
);

-- 청크 (Vector DB 연동)
CREATE TABLE IF NOT EXISTS chunks (
    id              TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    block_type      TEXT NOT NULL,
    content         TEXT NOT NULL,
    page_number     INTEGER,
    section_path    TEXT,
    table_data      TEXT,
    qdrant_id       TEXT
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_soil_doc ON soil_parameters(doc_id);
CREATE INDEX IF NOT EXISTS idx_soil_layer ON soil_parameters(layer_name);
CREATE INDEX IF NOT EXISTS idx_section_doc ON section_checks(doc_id);
CREATE INDEX IF NOT EXISTS idx_section_id ON section_checks(section_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(block_type);
