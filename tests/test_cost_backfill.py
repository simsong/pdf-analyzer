from pathlib import Path

from pdf_analyzer.db import Database
from pdf_analyzer.utils import normalize_question, utc_now_iso


def test_backfill_missing_usage_costs_uses_latest_pricing_snapshot(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    db.init_schema()
    now_iso = utc_now_iso()
    query_id = db.get_or_create_query(
        question="What happened?",
        normalized_question=normalize_question("What happened?"),
        model_name="gemini-3-flash-preview",
        prompt_version="v1",
        schema_version="v1",
        synthesis_prompt_version="v1",
        now_iso=now_iso,
    )
    db.upsert_document(
        sha256="a" * 64,
        file_size_bytes=100,
        page_count=1,
        canonical_filename="doc.pdf",
        now_iso=now_iso,
    )
    db.upsert_document_path(
        sha256="a" * 64,
        absolute_path=str(tmp_path / "doc.pdf"),
        relative_path="doc.pdf",
        original_filename="doc.pdf",
        now_iso=now_iso,
    )
    db.upsert_document_analysis(
        {
            "query_id": query_id,
            "document_sha256": "a" * 64,
            "status": "succeeded",
            "source_path": str(tmp_path / "doc.pdf"),
            "source_filename": "doc.pdf",
            "page_count": 1,
            "responsive": 1,
            "relevance_score": 10,
            "summary": "summary",
            "people": [],
            "places": [],
            "dates": [],
            "evidence_count": 0,
            "model_name": "gemini-3-flash-preview",
            "prompt_tokens": 1_000_000,
            "candidate_tokens": 2_000_000,
            "total_tokens": 3_000_000,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "started_at": now_iso,
            "completed_at": now_iso,
        }
    )
    db.upsert_synthesis(
        {
            "query_id": query_id,
            "status": "succeeded",
            "answer": "answer",
            "key_findings": [],
            "people": [],
            "places": [],
            "dates": [],
            "model_name": "gemini-3-flash-preview",
            "prompt_tokens": 1_000,
            "candidate_tokens": 2_000,
            "total_tokens": 3_000,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "started_at": now_iso,
            "completed_at": now_iso,
        }
    )

    db.backfill_missing_usage_costs(
        query_id=query_id,
        pricing_snapshot={
            "models": {
                "gemini-3-flash-preview": {
                    "standard": {
                        "input_usd_per_million_tokens": 0.5,
                        "output_usd_per_million_tokens": 3.0,
                    }
                }
            }
        },
    )

    usage_summary = db.calculate_query_usage_summary(query_id)
    assert usage_summary["prompt_tokens"] == 1_001_000
    assert usage_summary["candidate_tokens"] == 2_002_000
    assert round(usage_summary["input_cost_usd"], 6) == 0.5005
    assert round(usage_summary["output_cost_usd"], 6) == 6.006
    assert round(usage_summary["total_cost_usd"], 6) == 6.5065
