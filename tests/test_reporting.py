from pathlib import Path
from typing import Any

from pdf_analyzer.reporting import _build_responsive_documents_index, generate_reports


class EmptyReportDb:
    def report_document_rows(self, query_id: int) -> list[dict[str, Any]]:
        return []

    def report_evidence_rows(self, query_id: int) -> list[dict[str, Any]]:
        return []

    def report_failure_rows(self, query_id: int) -> list[dict[str, Any]]:
        return []

    def fetch_synthesis(self, query_id: int) -> None:
        return None

    def fetch_latest_object(self, object_name: str) -> None:
        return None

    def calculate_query_usage_summary(self, query_id: int) -> dict[str, int | float]:
        return {
            "prompt_tokens": 0,
            "candidate_tokens": 0,
            "total_tokens": 0,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
        }


def test_responsive_documents_index_includes_page_count() -> None:
    document_rows = [
        {
            "sha256": "abc123",
            "canonical_filename": "winter-study.pdf",
            "stored_page_count": 231,
            "responsive": 1,
            "summary": "A responsive document.",
        }
    ]
    evidence_rows = [
        {
            "document_sha256": "abc123",
            "anchor": "evidence-1",
            "page_start": 12,
            "page_label": "Page 12",
            "page_href": "pdfs/winter-study.pdf#page=12",
            "people": ["Clair W. Halligan"],
            "brief_summary": "A responsive evidence row.",
            "report_href": "pdfs/winter-study.pdf",
            "sort_key": (1960, 1, 1),
            "source_filename": "winter-study.pdf",
            "summary": "A responsive evidence row.",
        }
    ]

    rows = _build_responsive_documents_index(document_rows, evidence_rows)

    assert len(rows) == 1
    assert rows[0]["page_count"] == 231


def test_generate_reports_uses_custom_html_filename(tmp_path: Path) -> None:
    html_path, xlsx_path = generate_reports(
        db=EmptyReportDb(),
        query_id=1,
        output_dir=tmp_path,
        question="What happened?",
        project_name="Example",
        model_name="gemini-3-flash-preview",
        name_clustering_method="local",
        allow_gemini=False,
        report_html_filename="custom-report.html",
    )

    assert html_path == tmp_path / "custom-report.html"
    assert html_path.exists()
    assert not (tmp_path / "report.html").exists()
    assert xlsx_path == tmp_path / "report.xlsx"
    assert xlsx_path.exists()
