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
