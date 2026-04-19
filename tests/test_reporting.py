from pdf_analyzer.reporting import _build_responsive_documents_index


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
