# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from pdf_analyzer.main import main


def _write_text_pdf(path: Path, lines: list[str]) -> None:
    def pdf_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text_ops = ["BT", "/F1 16 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index > 0:
            text_ops.append("0 -22 Td")
        text_ops.append(f"({pdf_escape(line)}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(pdf)


@pytest.mark.gemini_live
def test_pdf_analyzer_live_single_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY is not set")

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "bridge-memo.pdf"
    _write_text_pdf(
        pdf_path,
        [
            "Archive note: The Cedar Bridge reopened in 2021.",
            "Alice Baker announced the reopening in Portland, Maine.",
            "This memo is directly responsive to questions about bridge reopening details.",
        ],
    )

    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: Live Gemini Bridge Test",
                "pdf_directory: ./pdfs",
                "output_directory: ./live-output",
                "question: Which bridge reopened, where, when, and who announced it?",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze",
            str(config_path),
            "--limit",
            "1",
            "--force",
        ],
    )
    exit_code = main()
    assert exit_code == 0

    output_dir = tmp_path / "live-output"
    database_path = output_dir / "pdf_analyzer.sqlite3"
    report_html = output_dir / "report.html"
    report_xlsx = output_dir / "report.xlsx"
    copied_config = output_dir / "project.yaml"

    assert database_path.exists()
    assert report_html.exists()
    assert report_xlsx.exists()
    assert copied_config.exists()

    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    analysis = conn.execute(
        """
        SELECT status, responsive, relevance_score, summary, people_json, places_json, dates_json
        FROM document_analyses
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert analysis is not None
    assert analysis["status"] == "succeeded"
    assert analysis["responsive"] == 1
    assert analysis["relevance_score"] >= 5

    lower_summary = (analysis["summary"] or "").casefold()
    assert "cedar bridge" in lower_summary or "bridge" in lower_summary

    people_json = analysis["people_json"] or "[]"
    places_json = analysis["places_json"] or "[]"
    dates_json = analysis["dates_json"] or "[]"
    merged_text = " ".join([people_json, places_json, dates_json, lower_summary]).casefold()
    assert "alice" in merged_text
    assert "portland" in merged_text or "maine" in merged_text
    assert "2021" in merged_text

    evidence = conn.execute(
        """
        SELECT page_start, page_end, summary, people_json, places_json, dates_json
        FROM analysis_evidence
        ORDER BY ordinal
        """
    ).fetchall()
    assert evidence
    evidence_blob = " ".join(
        " ".join(
            str(item[key] or "")
            for key in ("summary", "people_json", "places_json", "dates_json")
        )
        for item in evidence
    ).casefold()
    assert any(row["page_start"] in (1, None) for row in evidence)
    assert "cedar bridge" in evidence_blob or "bridge" in evidence_blob
    assert "alice" in evidence_blob
    assert "2021" in evidence_blob

    synthesis = conn.execute(
        """
        SELECT status, answer, key_findings_json, people_json, places_json, dates_json
        FROM project_syntheses
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert synthesis is not None
    assert synthesis["status"] == "succeeded"
    synthesis_blob = " ".join(str(synthesis[key] or "") for key in synthesis.keys()).casefold()
    assert "cedar bridge" in synthesis_blob or "bridge" in synthesis_blob
    assert "alice" in synthesis_blob
    assert "2021" in synthesis_blob

    html = report_html.read_text(encoding="utf-8").casefold()
    assert "cedar bridge" in html or "bridge" in html
    assert "alice" in html
    assert "2021" in html
