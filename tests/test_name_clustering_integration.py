# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

from pydantic import ValidationError

from pdf_analyzer.config import ProjectConfig
from pdf_analyzer.name_clustering import cluster_names, person_sort_key
from pdf_analyzer.reporting import _build_people_index
from tests.name_clustering_support import load_name_records


def test_project_config_defaults_name_clustering_to_local() -> None:
    config = ProjectConfig.model_validate(
        {
            "name": "Example",
            "pdf_directory": ".",
            "output_directory": ".",
            "question": "What happened?",
        }
    )
    assert config.name_clustering == "local"
    assert config.pdf_directory == [Path(".")]
    assert config.ignore_dirs_containing == [".pdfdata"]
    assert config.report_html_filename == "report.html"
    assert config.normalize_pdf is False
    assert config.flatten_pdf is False
    assert config.flatten_dpi == 300


def test_project_config_accepts_pdf_input_path_list() -> None:
    config = ProjectConfig.model_validate(
        {
            "name": "Example",
            "pdf_directory": ["./pdfs", "./more-pdfs"],
            "output_directory": ".",
            "question": "What happened?",
        }
    )

    assert config.pdf_directory == [Path("pdfs"), Path("more-pdfs")]


def test_project_config_accepts_single_ignore_marker() -> None:
    config = ProjectConfig.model_validate(
        {
            "name": "Example",
            "pdf_directory": ".",
            "output_directory": ".",
            "question": "What happened?",
            "ignore_dirs_containing": ".skip-pdfs",
        }
    )
    assert config.ignore_dirs_containing == [".skip-pdfs"]


def test_project_config_accepts_ignore_marker_list() -> None:
    config = ProjectConfig.model_validate(
        {
            "name": "Example",
            "pdf_directory": ".",
            "output_directory": ".",
            "question": "What happened?",
            "ignore_dirs_containing": [".pdfdata", ".skip-pdfs"],
        }
    )
    assert config.ignore_dirs_containing == [".pdfdata", ".skip-pdfs"]


def test_project_config_accepts_custom_report_html_filename() -> None:
    config = ProjectConfig.model_validate(
        {
            "name": "Example",
            "pdf_directory": ".",
            "output_directory": ".",
            "question": "What happened?",
            "report_html_filename": "wag-insider.html",
        }
    )
    assert config.report_html_filename == "wag-insider.html"


def test_project_config_accepts_pdf_normalization_options() -> None:
    config = ProjectConfig.model_validate(
        {
            "name": "Example",
            "pdf_directory": ".",
            "output_directory": ".",
            "question": "What happened?",
            "normalize_pdf": True,
            "flatten_pdf": True,
            "flatten_dpi": 400,
        }
    )

    assert config.normalize_pdf
    assert config.flatten_pdf
    assert config.flatten_dpi == 400


def test_project_config_rejects_report_html_path() -> None:
    try:
        ProjectConfig.model_validate(
            {
                "name": "Example",
                "pdf_directory": ".",
                "output_directory": ".",
                "question": "What happened?",
                "report_html_filename": "reports/wag-insider.html",
            }
        )
    except ValidationError as exc:
        assert "filename, not a path" in str(exc)
        return
    raise AssertionError("Expected report_html_filename path to be rejected")


def test_project_config_rejects_report_html_filename_with_outer_whitespace() -> None:
    try:
        ProjectConfig.model_validate(
            {
                "name": "Example",
                "pdf_directory": ".",
                "output_directory": ".",
                "question": "What happened?",
                "report_html_filename": "wag-insider.html ",
            }
        )
    except ValidationError as exc:
        assert "leading or trailing whitespace" in str(exc)
        return
    raise AssertionError("Expected report_html_filename with whitespace to be rejected")


def test_project_config_rejects_non_positive_flatten_dpi() -> None:
    try:
        ProjectConfig.model_validate(
            {
                "name": "Example",
                "pdf_directory": ".",
                "output_directory": ".",
                "question": "What happened?",
                "flatten_dpi": 0,
            }
        )
    except ValidationError as exc:
        assert "flatten_dpi must be positive" in str(exc)
        return
    raise AssertionError("Expected non-positive flatten_dpi to be rejected")


def test_project_config_rejects_directory_path_with_outer_whitespace() -> None:
    try:
        ProjectConfig.model_validate(
            {
                "name": "Example",
                "pdf_directory": ".",
                "output_directory": "pdf-insider ",
                "question": "What happened?",
            }
        )
    except ValidationError as exc:
        assert "leading or trailing whitespace" in str(exc)
        return
    raise AssertionError("Expected output_directory with trailing whitespace to be rejected")


def test_project_config_rejects_pdf_input_path_list_entry_with_outer_whitespace() -> None:
    try:
        ProjectConfig.model_validate(
            {
                "name": "Example",
                "pdf_directory": [".", "bad-input "],
                "output_directory": ".",
                "question": "What happened?",
            }
        )
    except ValidationError as exc:
        assert "pdf_directory paths must not have leading or trailing whitespace" in str(exc)
        return
    raise AssertionError("Expected pdf_directory list entry with trailing whitespace to be rejected")


def test_project_config_from_path_rejects_quoted_trailing_whitespace(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: Example",
                "pdf_directory: ./pdfs",
                'output_directory: "pdf-insider "',
                "question: What happened?",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        ProjectConfig.from_path(config_path)
    except SystemExit as exc:
        assert "output_directory must not have leading or trailing whitespace" in str(exc)
    else:
        raise AssertionError("Expected quoted trailing whitespace to be rejected")
    assert not (tmp_path / "pdf-insider ").exists()


def test_project_config_from_path_resolves_pdf_input_path_list(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_file = tmp_path / "single.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n")
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        "\n".join(
            [
                "name: Example",
                "pdf_directory:",
                "  - ./pdfs",
                "  - ./single.pdf",
                "output_directory: ./out",
                "question: What happened?",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = ProjectConfig.from_path(config_path)

    assert config.resolved_pdf_input_paths == [pdf_dir.resolve(), pdf_file.resolve()]
    assert config.resolved_output_directory == (tmp_path / "out").resolve()


def test_project_config_rejects_unknown_name_clustering_method() -> None:
    try:
        ProjectConfig.model_validate(
            {
                "name": "Example",
                "pdf_directory": ".",
                "output_directory": ".",
                "question": "What happened?",
                "name_clustering": "unknown",
            }
        )
    except ValidationError:
        return
    raise AssertionError("Expected invalid name_clustering value to be rejected")


def test_gemini_name_clustering_falls_back_to_local_when_gemini_disabled() -> None:
    result = cluster_names(
        load_name_records(),
        method="gemini",
        model_name="gemini-3-flash-preview",
        allow_gemini=False,
    )
    assert result.method == "local"


def test_people_index_uses_authoritative_name_and_keeps_matched_variant() -> None:
    evidence_rows = [
        {
            "anchor": "evidence-1",
            "date_label": "January 1960",
            "date_badge_label": "January 1960",
            "sort_key": (1960, 1, 1),
            "brief_summary": "Halligan joined the discussion.",
            "source_filename": "memo-a.pdf",
            "page_start": 12,
            "page_label": "Page 12",
            "report_href": "pdfs/memo-a.pdf",
            "people": ["Clair W. Halligan"],
            "raw_people": ["C. W. Halligan"],
            "canonical_name_by_raw": {"C. W. Halligan": "Clair W. Halligan"},
        },
        {
            "anchor": "evidence-2",
            "date_label": "February 1960",
            "date_badge_label": "February 1960",
            "sort_key": (1960, 2, 1),
            "brief_summary": "Baker replied to Halligan.",
            "source_filename": "memo-b.pdf",
            "page_start": 15,
            "page_label": "Page 15",
            "report_href": "pdfs/memo-b.pdf",
            "people": ["W. O. Baker", "Clair W. Halligan"],
            "raw_people": ["W. O. Baker", "Clair Halligan"],
            "canonical_name_by_raw": {
                "W. O. Baker": "W. O. Baker",
                "Clair Halligan": "Clair W. Halligan",
            },
        },
    ]

    people_index = _build_people_index(evidence_rows)

    assert [row["person"] for row in people_index] == ["W. O. Baker", "Clair W. Halligan"]
    assert people_index[1]["mentions"][0]["matched_names"] == ["C. W. Halligan"]
    assert people_index[1]["mentions"][1]["matched_names"] == ["Clair Halligan"]


def test_person_sort_key_sorts_by_last_name() -> None:
    names = ["Gordon N. Thayer", "Clair W. Halligan", "W. O. Baker"]
    assert sorted(names, key=person_sort_key) == [
        "W. O. Baker",
        "Clair W. Halligan",
        "Gordon N. Thayer",
    ]
