"""
Doaz Engineering RAG -- End-to-End Integration Test
Usage: uv run python scripts/e2e_test.py
"""
from __future__ import annotations

import json
import sys
import time

import httpx

API = "http://localhost:8000/api/v1"
QDRANT = "http://localhost:6333"
PDF_PATH = r"data/uploads/[2-2] 250122 흙막이보고서.pdf"

client = httpx.Client(timeout=1800.0)
results: dict[str, int | list[str]] = {"passed": 0, "failed": 0, "errors": []}


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        results["passed"] = int(results["passed"]) + 1
        print(f"  PASS: {name}")
    else:
        results["failed"] = int(results["failed"]) + 1
        assert isinstance(results["errors"], list)
        results["errors"].append(f"{name}: {detail}")
        print(f"  FAIL: {name} -- {detail}")


def sql_query(sql: str) -> dict:
    r = client.post(f"{API}/query/sql", json={"sql": sql})
    return r.json()


def run_query(query: str, doc_ids: list[str] | None = None) -> dict:
    payload: dict = {"query": query}
    if doc_ids:
        payload["doc_ids"] = doc_ids
    r = client.post(f"{API}/query/", json=payload, timeout=120.0)
    return r.json()


# ========================================
# PHASE 0: Prerequisites
# ========================================
print("\n" + "=" * 60)
print("PHASE 0: Prerequisites")
print("=" * 60)

r = client.get(f"{API}/health")
health = r.json()
check("Health endpoint reachable", r.status_code == 200)
check("Qdrant connected", health.get("qdrant") == "connected")
check("DB connected", health.get("db") == "connected")

r = client.get(f"{QDRANT}/collections")
check("Qdrant direct access", r.status_code == 200)


# ========================================
# PHASE 1: Document Upload
# ========================================
print("\n" + "=" * 60)
print("PHASE 1: Document Upload")
print("=" * 60)

# Clean prior data
existing = client.get(f"{API}/documents/").json()
if existing:
    print(f"  Cleaning {len(existing)} existing document(s)...")
    for doc in existing:
        client.delete(f"{API}/documents/{doc['id']}")

print(f"  Uploading PDF (this may take 1-5 minutes)...")
t0 = time.time()

with open(PDF_PATH, "rb") as f:
    r = client.post(
        f"{API}/documents/upload",
        files={"file": ("[2-2] 250122 \ud761\ub9c9\uc774\ubcf4\uace0\uc11c.pdf", f, "application/pdf")},
    )

elapsed = time.time() - t0
print(f"  Upload completed in {elapsed:.1f}s")

check("Upload status 200", r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

if r.status_code != 200:
    print("\nFATAL: Upload failed. Cannot continue.")
    sys.exit(1)

upload = r.json()
doc_id = upload.get("doc_id", "")
print(f"  doc_id:        {doc_id}")
print(f"  page_count:    {upload.get('page_count')}")
print(f"  block_count:   {upload.get('block_count')}")
print(f"  table_count:   {upload.get('table_count')}")
print(f"  vector_points: {upload.get('vector_points')}")
print(f"  status:        {upload.get('status')}")

check("doc_id exists", len(doc_id) > 0)
check("page_count > 0", upload.get("page_count", 0) > 0, f"got {upload.get('page_count')}")
check("block_count > 0", upload.get("block_count", 0) > 0, f"got {upload.get('block_count')}")
check("table_count >= 0", upload.get("table_count", 0) >= 0, f"got {upload.get('table_count')}")
check("vector_points > 0", upload.get("vector_points", 0) > 0, f"got {upload.get('vector_points')}")
check("status == indexed", upload.get("status") == "indexed", f"got {upload.get('status')}")


# ========================================
# PHASE 2: Database Verification
# ========================================
print("\n" + "=" * 60)
print("PHASE 2: Database Verification")
print("=" * 60)

# 2A: documents
docs = sql_query("SELECT id, filename, project_name, page_count FROM documents")
check("documents table has rows", len(docs.get("rows", [])) > 0)

# 2B: chunks
chunks = sql_query("SELECT block_type, COUNT(*) as cnt FROM chunks GROUP BY block_type")
chunk_rows = chunks.get("rows", [])
print(f"  Block types: {json.dumps(chunk_rows, ensure_ascii=False)}")
chunk_total = sum(r.get("cnt", 0) for r in chunk_rows)
check("chunks table has data", chunk_total > 0, f"got {chunk_total} total chunks")

# 2C: soil_parameters
soil = sql_query("SELECT layer_name, N_value, unit_weight, cohesion, friction_angle FROM soil_parameters LIMIT 5")
soil_rows = soil.get("rows", [])
print(f"  Soil parameters: {len(soil_rows)} rows (showing up to 5)")
for row in soil_rows:
    print(f"    {json.dumps(row, ensure_ascii=False)}")
check("soil_parameters has data", len(soil_rows) > 0,
      "No soil data. Check sql_saver.py header key mapping")

# 2D: section_checks (with section_id)
sections = sql_query(
    "SELECT section_id, excavation_depth, embedment_SF, overall_result "
    "FROM section_checks WHERE section_id IS NOT NULL LIMIT 10"
)
sec_rows = sections.get("rows", [])
print(f"  Section checks (named): {len(sec_rows)} rows")
for row in sec_rows:
    print(f"    {json.dumps(row, ensure_ascii=False)}")
check("section_checks has named sections", len(sec_rows) > 0,
      "No section summaries. Check section_aggregator.py regex")

# 2E: all check results
all_checks = sql_query("SELECT COUNT(*) as cnt FROM section_checks")
print(f"  Total section_checks rows: {all_checks.get('rows', [{}])[0].get('cnt', 0)}")

# 2F: anchor_design / material_allowables
anchors = sql_query("SELECT COUNT(*) as cnt FROM anchor_design")
materials = sql_query("SELECT COUNT(*) as cnt FROM material_allowables")
print(f"  anchor_design: {anchors.get('rows', [{}])[0].get('cnt', 0)} rows (not yet implemented)")
print(f"  material_allowables: {materials.get('rows', [{}])[0].get('cnt', 0)} rows (not yet implemented)")


# ========================================
# PHASE 3: Qdrant Verification
# ========================================
print("\n" + "=" * 60)
print("PHASE 3: Qdrant Verification")
print("=" * 60)

r = client.get(f"{QDRANT}/collections/doaz_eng_rag")
check("Collection exists", r.status_code == 200)

if r.status_code == 200:
    coll = r.json().get("result", {})
    points_count = coll.get("points_count", 0)
    print(f"  Points in collection: {points_count}")
    check("points_count > 0", points_count > 0, f"got {points_count}")

    vec_cfg = coll.get("config", {}).get("params", {}).get("vectors", {})
    vec_size = vec_cfg.get("size", 0)
    check("Vector dim == 3072", vec_size == 3072, f"got {vec_size}")

# Scroll check
r = client.post(
    f"{QDRANT}/collections/doaz_eng_rag/points/scroll",
    json={"limit": 1, "with_payload": True, "with_vector": False},
)
if r.status_code == 200:
    pts = r.json().get("result", {}).get("points", [])
    if pts:
        payload = pts[0].get("payload", {})
        for field in ["doc_id", "content", "block_type", "page_number"]:
            check(f"Payload has '{field}'", field in payload, f"keys: {list(payload.keys())}")


# ========================================
# PHASE 4: Query Tests (5 types)
# ========================================
print("\n" + "=" * 60)
print("PHASE 4: Query Tests")
print("=" * 60)

queries = [
    {
        "id": "4A", "label": "NUMERICAL",
        "query": "SEC-1O \uad74\ucc29\uae4a\uc774\ub294?",
        "expect_type": "numerical",
        "expect_sql": True, "expect_vector": False,
    },
    {
        "id": "4B", "label": "CONCEPTUAL",
        "query": "\ud761\ub9c9\uc774 \uac00\uc2dc\uc124 \uc124\uacc4 \ubc29\ubc95 \uc124\uba85",
        "expect_type": "conceptual",
        "expect_sql": False, "expect_vector": True,
    },
    {
        "id": "4C", "label": "COMPLIANCE",
        "query": "SEC-1O \ubcbd\uccb4\ubcc0\uc704 \ub9cc\uc871 \uc5ec\ubd80",
        "expect_type": "compliance",
        "expect_sql": True, "expect_vector": True,
    },
    {
        "id": "4D", "label": "COMPARATIVE",
        "query": "\uac01 \ub2e8\uba74\ubcc4 \uad74\ucc29\uae4a\uc774 \ube44\uad50",
        "expect_type": "comparative",
        "expect_sql": True, "expect_vector": True,
    },
    {
        "id": "4E", "label": "MULTI_HOP",
        "query": "SEC-1O \uadfc\uc785\uc7a5 \uc548\uc804\uc728 \uc0b0\uc815 \uadfc\uac70",
        "expect_type": "multi_hop",
        "expect_sql": True, "expect_vector": True,
    },
]

for q in queries:
    print(f"\n  --- {q['id']}: {q['label']} ---")
    print(f"  Query: {q['query']}")

    try:
        resp = run_query(q["query"], [doc_id])
    except Exception as e:
        check(f"{q['id']} query succeeded", False, str(e))
        continue

    qtype = resp.get("query_type", "")
    sql_q = resp.get("sql_query")
    vec_c = resp.get("vector_count", 0)
    answer = resp.get("answer", "")
    sources = resp.get("sources", [])

    print(f"  query_type:   {qtype}")
    print(f"  sql_query:    {sql_q}")
    print(f"  vector_count: {vec_c}")
    print(f"  answer:       {answer[:200]}...")
    print(f"  sources:      {len(sources)} items")

    check(f"{q['id']} query_type == {q['expect_type']}", qtype == q["expect_type"],
          f"got {qtype}")

    if q["expect_sql"]:
        check(f"{q['id']} sql_query not null", sql_q is not None)
    else:
        check(f"{q['id']} sql_query is null", sql_q is None, f"got {sql_q}")

    if q["expect_vector"]:
        check(f"{q['id']} vector_count > 0", vec_c > 0, f"got {vec_c}")
    else:
        check(f"{q['id']} vector_count == 0", vec_c == 0, f"got {vec_c}")

    check(f"{q['id']} answer not empty", len(answer) > 10, f"got {len(answer)} chars")


# ========================================
# SUMMARY
# ========================================
print("\n" + "=" * 60)
passed = int(results["passed"])
failed = int(results["failed"])
print(f"RESULTS: {passed} passed, {failed} failed")
if results["errors"]:
    print(f"\nFailed checks:")
    assert isinstance(results["errors"], list)
    for err in results["errors"]:
        print(f"  - {err}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
