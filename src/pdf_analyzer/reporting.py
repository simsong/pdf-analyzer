import re
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from .utils import clone_or_copy_file, coerce_json_list, parse_sort_year, slugify, unique_copy_name

_MONTH_NUMBERS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True))
_EXACT_DATE_PATTERN = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\.?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+"
    r"(?P<year>(?:18|19|20)\d{2})\b",
    re.IGNORECASE,
)
_MONTH_YEAR_PATTERN = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\.?\s+(?P<year>(?:18|19|20)\d{{2}})\b",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b(?P<year>(?:18|19|20)\d{2})\b")
_UNSORTED_DATE = (9999, 12, 31)


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


def _overlaps_existing(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < other_end and other_start < span[1] for other_start, other_end in occupied)


def _extract_date_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []

    for match in _EXACT_DATE_PATTERN.finditer(text):
        month = _MONTH_NUMBERS[match.group("month").casefold().rstrip(".")]
        day = int(match.group("day"))
        year = int(match.group("year"))
        occupied.append(match.span())
        candidates.append(
            {
                "sort_key": (year, month, day),
                "label": f"{match.group('month').rstrip('.').title()} {day}, {year}",
            }
        )

    for match in _MONTH_YEAR_PATTERN.finditer(text):
        if _overlaps_existing(match.span(), occupied):
            continue
        month = _MONTH_NUMBERS[match.group("month").casefold().rstrip(".")]
        year = int(match.group("year"))
        occupied.append(match.span())
        candidates.append(
            {
                "sort_key": (year, month, 1),
                "label": f"{match.group('month').rstrip('.').title()} {year}",
            }
        )

    for match in _YEAR_PATTERN.finditer(text):
        if _overlaps_existing(match.span(), occupied):
            continue
        year = int(match.group("year"))
        candidates.append({"sort_key": (year, 1, 1), "label": str(year)})

    return candidates


def _best_date_metadata(values: list[str], fallback_text: str = "") -> tuple[tuple[int, int, int], str]:
    text = " ".join(value for value in values if value)
    if fallback_text:
        text = f"{text} {fallback_text}".strip()
    candidates = _extract_date_candidates(text)
    if not candidates:
        return _UNSORTED_DATE, "Undated"
    best = min(candidates, key=lambda candidate: candidate["sort_key"])
    return best["sort_key"], best["label"]


def _truncate_text(text: str, limit: int = 105) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _key_person_for_row(row: dict[str, Any]) -> str:
    if row["people"]:
        return row["people"][0]
    if row["document_people"]:
        return row["document_people"][0]
    return "None"


def _pdf_page_href(report_href: str | None, page_number: int | None) -> str | None:
    if not report_href:
        return None
    if not isinstance(page_number, int) or page_number < 1:
        return report_href
    return f"{report_href}#page={page_number}"


def _format_time_period(mentions: list[dict[str, Any]]) -> str:
    if not mentions:
        return "Undated"
    first_label = mentions[0]["date_label"]
    last_label = mentions[-1]["date_label"]
    if first_label == last_label:
        return first_label
    return f"{first_label} to {last_label}"


def _evidence_sort_key(row: dict[str, Any]) -> tuple[tuple[int, int, int], int, str, str]:
    page = row["page_number"] if isinstance(row["page_number"], int) else 10**9
    return (row["sort_key"], page, row["source_filename"].casefold(), row["summary"].casefold())


def _build_people_index(evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in evidence_rows:
        seen_in_row: set[str] = set()
        for person in row["people"]:
            cleaned = person.strip()
            if not cleaned or cleaned in seen_in_row:
                continue
            seen_in_row.add(cleaned)
            grouped.setdefault(cleaned, []).append(
                {
                    "anchor": row["anchor"],
                    "date_label": row["date_label"],
                    "sort_key": row["sort_key"],
                    "brief_summary": row["brief_summary"],
                    "source_filename": row["source_filename"],
                    "page_number": row["page_number"],
                    "page_href": _pdf_page_href(row.get("report_href"), row["page_number"]),
                }
            )

    used_toggle_ids: set[str] = set()
    people_index: list[dict[str, Any]] = []
    for person, mentions in grouped.items():
        mentions.sort(
            key=lambda mention: (
                mention["sort_key"],
                mention["page_number"] if isinstance(mention["page_number"], int) else 10**9,
                mention["source_filename"].casefold(),
                mention["brief_summary"].casefold(),
            )
        )
        toggle_id = unique_copy_name(f"person-{slugify(person)}", used_toggle_ids)
        people_index.append(
            {
                "person": person,
                "mention_count": len(mentions),
                "time_period": _format_time_period(mentions),
                "mentions": mentions,
                "toggle_id": toggle_id,
            }
        )

    people_index.sort(key=lambda row: (-row["mention_count"], row["person"].casefold()))
    return people_index


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
    if pdf_dir.exists():
        shutil.rmtree(pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    document_rows = [dict(row) for row in db.report_document_rows(query_id)]
    evidence_rows_raw = [dict(row) for row in db.report_evidence_rows(query_id)]
    failure_rows = [dict(row) for row in db.report_failure_rows(query_id)]
    synthesis_row = db.fetch_synthesis(query_id)

    responsive_sha256s = {row["document_sha256"] for row in evidence_rows_raw}
    responsive_sha256s.update(row["sha256"] for row in document_rows if row.get("responsive") == 1)

    used_names: set[str] = set()
    linked_files: dict[str, str] = {}
    for row in document_rows:
        if row["sha256"] not in responsive_sha256s:
            continue
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
        sort_key, date_label = _best_date_metadata(dates, row["evidence_summary"] or row["document_summary"] or "")
        evidence_rows.append(
            {
                "document_sha256": row["document_sha256"],
                "source_filename": row["source_filename"],
                "source_path": row["source_path"],
                "document_summary": row["document_summary"] or "",
                "relevance_score": row["relevance_score"],
                "page_number": row["page_number"],
                "summary": row["evidence_summary"],
                "brief_summary": _truncate_text(row["evidence_summary"] or row["document_summary"] or ""),
                "people": people,
                "places": places,
                "dates": dates,
                "document_people": document_people,
                "document_places": document_places,
                "document_dates": document_dates,
                "date_label": date_label,
                "sort_key": sort_key,
                "key_person": "",
                "anchor": "",
                "report_href": linked_files.get(row["document_sha256"]),
                "page_href": _pdf_page_href(
                    linked_files.get(row["document_sha256"]),
                    row["page_number"],
                ),
            }
        )
    evidence_rows.sort(key=_evidence_sort_key)
    for index, row in enumerate(evidence_rows, start=1):
        row["anchor"] = f"evidence-{index}"
        row["key_person"] = _key_person_for_row(row)

    people_index = _build_people_index(evidence_rows)
    unanalyzed_rows = [row for row in document_rows if row.get("status") != "succeeded"]

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

    total_pages_scanned = (
        int(run_summary["scanned_pages"])
        if run_summary and run_summary.get("scanned_pages") is not None
        else sum(int(row.get("stored_page_count") or 0) for row in document_rows)
    )
    summary = {
        "total_documents": len(document_rows),
        "total_pages_scanned": total_pages_scanned,
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
        people_index=people_index,
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
            "sort_date",
            "sort_year",
            "source_filename",
            "page_number",
            "key_person",
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
                _safe_excel(row["date_label"]),
                _safe_excel(parse_sort_year(row["dates"], row["summary"])),
                _safe_excel(row["source_filename"]),
                _safe_excel(row["page_number"]),
                _safe_excel(row["key_person"]),
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
