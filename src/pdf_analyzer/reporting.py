import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from .utils import clone_or_copy_file, coerce_json_list, parse_sort_year, unique_copy_name


def _safe_excel(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def _safe_display_list(values: list[str]) -> str:
    return "; ".join(value for value in values if value)


def _build_template_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _evidence_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    year = parse_sort_year(row["dates"], row["summary"]) or 9999
    page = row["page_number"] if isinstance(row["page_number"], int) else 10**9
    return (year, page, row["source_filename"].casefold(), row["summary"].casefold())


def generate_reports(
    *,
    db,
    query_id: int,
    output_dir: Path,
    question: str,
    project_name: str,
    run_summary: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir = output_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    document_rows = [dict(row) for row in db.report_document_rows(query_id)]
    evidence_rows_raw = [dict(row) for row in db.report_evidence_rows(query_id)]
    failure_rows = [dict(row) for row in db.report_failure_rows(query_id)]
    synthesis_row = db.fetch_synthesis(query_id)
    used_names: set[str] = set()
    linked_files: dict[str, str] = {}
    for row in document_rows:
        source_path = row.get("source_path")
        if not source_path:
            continue
        source = Path(source_path)
        if not source.exists():
            continue
        report_name = unique_copy_name(row["canonical_filename"], used_names)
        target = pdf_dir / report_name
        clone_or_copy_file(source, target)
        linked_files[row["sha256"]] = f"pdfs/{report_name}"

    evidence_rows: list[dict[str, Any]] = []
    for row in evidence_rows_raw:
        dates = coerce_json_list(row["dates_json"])
        people = coerce_json_list(row["people_json"])
        places = coerce_json_list(row["places_json"])
        document_people = coerce_json_list(row["document_people_json"])
        document_places = coerce_json_list(row["document_places_json"])
        document_dates = coerce_json_list(row["document_dates_json"])
        evidence_rows.append(
            {
                "document_sha256": row["document_sha256"],
                "source_filename": row["source_filename"],
                "source_path": row["source_path"],
                "document_summary": row["document_summary"] or "",
                "relevance_score": row["relevance_score"],
                "page_number": row["page_number"],
                "summary": row["evidence_summary"],
                "people": people,
                "places": places,
                "dates": dates,
                "document_people": document_people,
                "document_places": document_places,
                "document_dates": document_dates,
                "report_href": linked_files.get(row["document_sha256"]),
            }
        )
    evidence_rows.sort(key=_evidence_sort_key)

    unanalyzed_rows = [
        row for row in document_rows if row.get("status") != "succeeded"
    ]

    synthesis_payload = None
    if synthesis_row is not None and synthesis_row["status"] == "succeeded":
        synthesis_payload = {
            "answer": synthesis_row["answer"],
            "key_findings": coerce_json_list(synthesis_row["key_findings_json"]),
            "people": coerce_json_list(synthesis_row["people_json"]),
            "places": coerce_json_list(synthesis_row["places_json"]),
            "dates": coerce_json_list(synthesis_row["dates_json"]),
            "reasoning_notes": synthesis_row["reasoning_notes"],
        }

    summary = {
        "total_documents": len(document_rows),
        "responsive_evidence_rows": len(evidence_rows),
        "responsive_documents": sum(1 for row in document_rows if row.get("responsive") == 1),
        "failed_documents": len(failure_rows),
        "unanalyzed_documents": len(unanalyzed_rows),
    }
    env = _build_template_environment()
    template = env.get_template("report.html.jinja")
    html = template.render(
        title=project_name,
        question=question,
        summary=summary,
        run_summary=run_summary,
        synthesis=synthesis_payload,
        evidence_rows=evidence_rows,
        document_rows=document_rows,
        unanalyzed_rows=unanalyzed_rows,
        failure_rows=failure_rows,
    )
    html_path = output_dir / "report.html"
    html_path.write_text(html.rstrip() + "\n", encoding="utf-8")

    workbook = Workbook()
    evidence_sheet = workbook.active
    evidence_sheet.title = "Evidence"
    evidence_sheet.append(
        [
            "sort_year",
            "source_filename",
            "page_number",
            "summary",
            "people",
            "places",
            "dates",
            "document_summary",
            "relevance_score",
            "source_path",
        ]
    )
    for row in evidence_rows:
        evidence_sheet.append(
            [
                _safe_excel(parse_sort_year(row["dates"], row["summary"])),
                _safe_excel(row["source_filename"]),
                _safe_excel(row["page_number"]),
                _safe_excel(row["summary"]),
                _safe_excel(_safe_display_list(row["people"])),
                _safe_excel(_safe_display_list(row["places"])),
                _safe_excel(_safe_display_list(row["dates"])),
                _safe_excel(row["document_summary"]),
                _safe_excel(row["relevance_score"]),
                _safe_excel(row["source_path"]),
            ]
        )

    documents_sheet = workbook.create_sheet("Documents")
    documents_sheet.append(
        [
            "source_filename",
            "relative_path",
            "status",
            "responsive",
            "relevance_score",
            "summary",
            "people",
            "places",
            "dates",
            "evidence_count",
            "failure_type",
            "error_text",
        ]
    )
    for row in document_rows:
        documents_sheet.append(
            [
                _safe_excel(row["canonical_filename"]),
                _safe_excel(row.get("relative_path")),
                _safe_excel(row.get("status")),
                _safe_excel(row.get("responsive")),
                _safe_excel(row.get("relevance_score")),
                _safe_excel(row.get("summary")),
                _safe_excel(_safe_display_list(coerce_json_list(row.get("people_json")))),
                _safe_excel(_safe_display_list(coerce_json_list(row.get("places_json")))),
                _safe_excel(_safe_display_list(coerce_json_list(row.get("dates_json")))),
                _safe_excel(row.get("evidence_count")),
                _safe_excel(row.get("failure_type")),
                _safe_excel(row.get("error_text")),
            ]
        )

    failures_sheet = workbook.create_sheet("Failures")
    failures_sheet.append(
        ["source_filename", "status", "failure_type", "error_text", "source_path", "completed_at"]
    )
    for row in failure_rows:
        failures_sheet.append(
            [
                _safe_excel(row.get("source_filename")),
                _safe_excel(row.get("status")),
                _safe_excel(row.get("failure_type")),
                _safe_excel(row.get("error_text")),
                _safe_excel(row.get("source_path")),
                _safe_excel(row.get("completed_at")),
            ]
        )

    xlsx_path = output_dir / "report.xlsx"
    workbook.save(xlsx_path)
    return html_path, xlsx_path
