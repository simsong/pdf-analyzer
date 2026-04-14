import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


DEFAULT_INPUT_JSONL = "output.jsonl"
DEFAULT_TEMPLATE = "report_template.html"
UNKNOWN_YEAR = 9999


@dataclass
class SourceMention:
    sort_year: int | None
    sort_label: str
    time_period: str
    page_numbers: list[int]
    score: int | None
    source_file: str
    raw_source_file: str
    is_chunk_source: bool
    analyzed_file: str
    page_count: int | None
    model_version: str
    compression_method: str | None
    processing_time: float | None
    archived_at: str


@dataclass
class ConnectionOccurrence:
    sort_year: int | None
    sort_label: str
    who: str
    what: str
    mention: SourceMention


@dataclass
class FactoredWhat:
    sort_year: int | None
    sort_label: str
    what: str
    time_periods: list[str]
    page_numbers: list[int]
    scores: list[int]
    source_files: list[str]
    mentions: list[SourceMention]


@dataclass
class FactoredConnection:
    sort_year: int | None
    sort_label: str
    who: str
    scores: list[int]
    facts: list[FactoredWhat]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render an HTML report from analyzer2 JSONL output."
    )
    parser.add_argument(
        "jsonl",
        nargs="?",
        default=DEFAULT_INPUT_JSONL,
        help="Path to the analyzer JSONL archive. Defaults to output.jsonl.",
    )
    parser.add_argument(
        "--template",
        default=DEFAULT_TEMPLATE,
        help="Path to the Jinja2 HTML template.",
    )
    parser.add_argument(
        "--output",
        help="Write the rendered HTML report to this path. Defaults to stdout.",
    )
    parser.add_argument(
        "--report-debug",
        action="store_true",
        help="Include debug-only fields in the rendered report.",
    )
    return parser.parse_args()


def ensure_file_exists(path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise SystemExit(f"{label} not found: {resolved}")
    if not resolved.is_file():
        raise SystemExit(f"{label} is not a file: {resolved}")
    return resolved


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_name(value: str) -> str:
    return normalize_text(value).casefold()


def canonical_source_file(value: str) -> str:
    normalized = normalize_text(value)
    if not normalized.lower().endswith(".pdf"):
        return normalized
    return re.sub(r"_chunk_[^.]+(?=\.pdf$)", "", normalized, flags=re.IGNORECASE)


def is_chunk_filename(value: str) -> bool:
    return bool(re.search(r"_chunk_[^.]+\.pdf$", normalize_text(value), flags=re.IGNORECASE))


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def dedupe_sorted_pages(page_lists: list[list[int]]) -> list[int]:
    pages: set[int] = set()
    for page_list in page_lists:
        for page in page_list:
            if isinstance(page, int):
                pages.add(page)
    return sorted(pages)


def event_tokens(value: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "began",
        "class",
        "co",
        "company",
        "for",
        "from",
        "graduate",
        "graduated",
        "he",
        "in",
        "inc",
        "louisiana",
        "mit",
        "new",
        "of",
        "orleans",
        "production",
        "recent",
        "reported",
        "that",
        "the",
        "who",
        "working",
    }
    tokens = re.findall(r"[a-z0-9']+", normalize_text(value).casefold())
    return {
        token
        for token in tokens
        if len(token) > 2 and token not in stopwords
    }


def same_event(existing_what: str, new_what: str, existing_mentions: list[SourceMention], new_mention: SourceMention) -> bool:
    # Placeholder heuristic for event clustering. The next step can replace this
    # with an LLM-backed decision without changing the rest of the report pipeline.
    existing_normalized = normalize_text(existing_what).casefold()
    new_normalized = normalize_text(new_what).casefold()
    if existing_normalized == new_normalized:
        return True

    existing_time_periods = {
        normalize_text(mention.time_period).casefold()
        for mention in existing_mentions
        if normalize_text(mention.time_period)
    }
    new_time_period = normalize_text(new_mention.time_period).casefold()
    if existing_time_periods and new_time_period and new_time_period not in existing_time_periods:
        return False

    existing_tokens = event_tokens(existing_what)
    new_tokens = event_tokens(new_what)
    if not existing_tokens or not new_tokens:
        return False

    overlap = existing_tokens & new_tokens
    overlap_ratio = len(overlap) / min(len(existing_tokens), len(new_tokens))
    return len(overlap) >= 2 and overlap_ratio >= 0.6


def extract_sort_year(time_period: str) -> int | None:
    matches = re.findall(r"\b(?:18|19|20)\d{2}\b", time_period or "")
    if not matches:
        return None
    return min(int(match) for match in matches)


def sort_key_year(year: int | None) -> int:
    return year if year is not None else UNKNOWN_YEAR


def format_archived_at(timestamp: Any) -> str:
    if not isinstance(timestamp, (int, float)):
        return ""
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def load_occurrences(jsonl_path: Path) -> list[ConnectionOccurrence]:
    occurrences: list[ConnectionOccurrence] = []
    with jsonl_path.open() as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"Could not parse JSON on line {line_number} of {jsonl_path}: {exc}"
                ) from exc

            data = entry.get("data") or {}
            connections = data.get("connections") or []
            score = data.get("score")
            raw_source_file = normalize_text(entry.get("filename") or "")
            source_file = canonical_source_file(raw_source_file)
            analyzed_file = normalize_text(entry.get("analyzed_filename") or source_file)
            page_count = entry.get("page_count")
            model_version = normalize_text(entry.get("model_version") or "")
            compression_method = normalize_text(entry.get("compression_method") or "")
            processing_time = entry.get("processing_time")
            archived_at = format_archived_at(entry.get("timestamp"))

            for connection in connections:
                who = normalize_text(connection.get("who") or "")
                what = normalize_text(connection.get("what") or "")
                time_period = normalize_text(connection.get("time_period") or "")
                if not who:
                    continue

                sort_year = extract_sort_year(time_period)
                occurrences.append(
                    ConnectionOccurrence(
                        sort_year=sort_year,
                        sort_label=str(sort_year) if sort_year is not None else "Unknown",
                        who=who,
                        what=what,
                        mention=SourceMention(
                            sort_year=sort_year,
                            sort_label=str(sort_year) if sort_year is not None else "Unknown",
                            time_period=time_period,
                            page_numbers=connection.get("page_numbers") or [],
                            score=score,
                            source_file=source_file,
                            raw_source_file=raw_source_file,
                            is_chunk_source=is_chunk_filename(raw_source_file),
                            analyzed_file=analyzed_file,
                            page_count=page_count,
                            model_version=model_version,
                            compression_method=compression_method or None,
                            processing_time=processing_time,
                            archived_at=archived_at,
                        ),
                    )
                )

    occurrences.sort(
        key=lambda occurrence: (
            sort_key_year(occurrence.sort_year),
            occurrence.mention.time_period,
            occurrence.mention.source_file,
            occurrence.who,
            occurrence.what,
        )
    )
    return occurrences


def factor_connections(occurrences: list[ConnectionOccurrence]) -> list[FactoredConnection]:
    grouped: dict[str, dict[str, Any]] = {}

    for occurrence in occurrences:
        person_key = normalize_name(occurrence.who)
        person = grouped.get(person_key)
        if person is None:
            person = {
                "who": occurrence.who,
                "mentions": [],
                "facts": [],
            }
            grouped[person_key] = person

        person["mentions"].append(occurrence.mention)

        fact = None
        for existing_fact in person["facts"]:
            if same_event(
                existing_fact["what"],
                occurrence.what,
                existing_fact["mentions"],
                occurrence.mention,
            ):
                fact = existing_fact
                break
        if fact is None:
            fact = {"what": occurrence.what, "mentions": []}
            person["facts"].append(fact)
        fact["mentions"].append(occurrence.mention)

    factored: list[FactoredConnection] = []
    for person in grouped.values():
        mentions: list[SourceMention] = sorted(
            person["mentions"],
            key=lambda mention: (
                sort_key_year(mention.sort_year),
                mention.time_period,
                mention.source_file,
            ),
        )
        sort_year = min(
            (mention.sort_year for mention in mentions if mention.sort_year is not None),
            default=None,
        )

        facts: list[FactoredWhat] = []
        for fact in person["facts"]:
            fact_mentions: list[SourceMention] = sorted(
                fact["mentions"],
                key=lambda mention: (
                    sort_key_year(mention.sort_year),
                    mention.time_period,
                    mention.source_file,
                ),
            )
            display_page_mentions = [
                mention for mention in fact_mentions if not mention.is_chunk_source
            ] or fact_mentions
            fact_sort_year = min(
                (mention.sort_year for mention in fact_mentions if mention.sort_year is not None),
                default=None,
            )
            facts.append(
                FactoredWhat(
                    sort_year=fact_sort_year,
                    sort_label=str(fact_sort_year) if fact_sort_year is not None else "Unknown",
                    what=fact["what"],
                    time_periods=dedupe_preserve_order(
                        [mention.time_period for mention in fact_mentions]
                    ),
                    page_numbers=dedupe_sorted_pages(
                        [mention.page_numbers for mention in display_page_mentions]
                    ),
                    scores=sorted(
                        {mention.score for mention in fact_mentions if mention.score is not None}
                    ),
                    source_files=dedupe_preserve_order(
                        [mention.source_file for mention in fact_mentions]
                    ),
                    mentions=fact_mentions,
                )
            )

        facts.sort(
            key=lambda fact: (
                sort_key_year(fact.sort_year),
                fact.what,
            )
        )

        factored.append(
            FactoredConnection(
                sort_year=sort_year,
                sort_label=str(sort_year) if sort_year is not None else "Unknown",
                who=person["who"],
                scores=sorted(
                    {mention.score for mention in mentions if mention.score is not None}
                ),
                facts=facts,
            )
        )

    factored.sort(
        key=lambda person: (
            sort_key_year(person.sort_year),
            person.who,
        )
    )
    return factored


def build_environment(template_path: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_report(
    template_path: Path,
    connections: list[FactoredConnection],
    source_jsonl: Path,
    *,
    debug: bool,
) -> str:
    env = build_environment(template_path)
    template = env.get_template(template_path.name)
    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return template.render(
        title="MIT Louisiana Connections Report",
        generated_at=generated_at,
        source_jsonl=str(source_jsonl),
        connection_count=len(connections),
        fact_count=sum(len(connection.facts) for connection in connections),
        mention_count=sum(
            len(fact.mentions)
            for connection in connections
            for fact in connection.facts
        ),
        connections=connections,
        debug=debug,
    )


def write_output(rendered_html: str, output_path: Path | None) -> None:
    if output_path is None:
        sys.stdout.write(rendered_html)
        if not rendered_html.endswith("\n"):
            sys.stdout.write("\n")
        return

    resolved = output_path.expanduser().resolve()
    resolved.write_text(rendered_html)


def main() -> int:
    args = parse_args()
    jsonl_path = ensure_file_exists(Path(args.jsonl), label="JSONL archive")
    template_path = ensure_file_exists(Path(args.template), label="Template")
    occurrences = load_occurrences(jsonl_path)
    connections = factor_connections(occurrences)
    rendered_html = render_report(
        template_path,
        connections,
        jsonl_path,
        debug=args.report_debug,
    )
    output_path = Path(args.output) if args.output else None
    write_output(rendered_html, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
