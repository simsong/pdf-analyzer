import argparse
import csv
import logging
import os
import shelve
import time
from functools import cached_property
from pathlib import Path

from google import genai
from google.genai import errors, types
from google.genai.types import File, GenerateContentResponseUsageMetadata
from pydantic import BaseModel, Field
from pypdf import PdfReader


PROMPT = (
    "You are a precise archival researcher. Analyze the provided PDF from the "
    "archives of the Massachusetts Institute of Technology and determine the "
    "probability (0-10) that it contains significant connections between MIT "
    "and the state of Louisiana or a city therein."
)
DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_CACHE = "google_cache.shelf"
DEFAULT_OUTPUT_JSONL = "output.jsonl"
DEFAULT_COST_CSV = "cost.csv"
PDF_SIZE_LIMIT_BYTES = 50 * 1024 * 1024
CACHE_TTL_SECONDS = 47 * 3600

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class LouisianaConnection(BaseModel):
    who: str = Field(description="Name of the person, family or Louisiana company")
    what: str = Field(description="The nature of the connection to MIT")
    time_period: str = Field(description="The relevant years or era")
    page_numbers: list[int] = Field(
        description="A list of 1-based page numbers in the PDF where this information appears"
    )


class AnalysisResult(BaseModel):
    score: int = Field(description="Probability score from 0 to 10")
    connections: list[LouisianaConnection] = Field(default_factory=list)


class ArchiveEntry(BaseModel):
    filename: str
    page_count: int | None = None
    timestamp: float
    processing_time: float
    data: AnalysisResult
    usage: GenerateContentResponseUsageMetadata | None = None
    model_version: str
    uploaded_filename: str
    uploaded_bytes: int


class PDFInspector:
    def __init__(self, pdf_path: Path):
        self.pdf_path = pdf_path

    @cached_property
    def file_size(self) -> int:
        return self.pdf_path.stat().st_size

    @cached_property
    def page_count(self) -> int | None:
        try:
            reader = PdfReader(str(self.pdf_path))
        except Exception as exc:
            logger.warning("Could not inspect %s with pypdf: %s", self.pdf_path, exc)
            return None
        return len(reader.pages)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze an MIT PDF for Louisiana connections via Gemini."
    )
    parser.add_argument("pdf", help="Path to the PDF file to analyze")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name")
    parser.add_argument(
        "--cache",
        default=DEFAULT_CACHE,
        help="Shelf file used to cache uploaded Gemini file handles",
    )
    parser.add_argument(
        "--output-jsonl",
        default=DEFAULT_OUTPUT_JSONL,
        help="Append the archived result to this JSONL file",
    )
    parser.add_argument(
        "--cost-csv",
        default=DEFAULT_COST_CSV,
        help="Append prompt/candidate/total token counts to this CSV file",
    )
    parser.add_argument(
        "--list-server-files",
        action="store_true",
        help="List currently active Gemini server-side files before analysis",
    )
    return parser.parse_args()


def require_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY is not set. Export it before running analyzer2.py."
        )
    return api_key


def build_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def list_files_on_server(client: genai.Client) -> None:
    print("Currently active files on server:")
    for remote_file in client.files.list():
        print(
            f"- {remote_file.display_name} ({remote_file.name}) | "
            f"Expires: {remote_file.expiration_time}"
        )


def ensure_pdf_exists(pdf_path: Path) -> Path:
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    if not pdf_path.is_file():
        raise SystemExit(f"Path is not a file: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise SystemExit(f"Expected a PDF file, got: {pdf_path}")
    return pdf_path


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def should_skip_pdf(pdf_path: Path, inspector: PDFInspector) -> bool:
    if inspector.file_size <= PDF_SIZE_LIMIT_BYTES:
        return False

    page_count = inspector.page_count
    page_text = f", {page_count} pages" if page_count is not None else ""
    logger.info(
        "Skipping %s: %s%s exceeds Gemini's 50 MB PDF limit.",
        pdf_path,
        human_size(inspector.file_size),
        page_text,
    )
    return True


def get_cached_file(
    client: genai.Client, local_path: Path, shelf_name: str = DEFAULT_CACHE
) -> File:
    abs_path = str(local_path.resolve())

    with shelve.open(shelf_name, writeback=True) as shelf:
        if abs_path in shelf:
            entry = shelf[abs_path]
            if time.time() - entry["timestamp"] < CACHE_TTL_SECONDS:
                try:
                    return client.files.get(name=entry["name"])
                except errors.ClientError:
                    logger.info("Cached Gemini file is no longer available. Re-uploading.")
            else:
                logger.info("Cached Gemini file expired. Re-uploading.")

        logger.info("Uploading %s", abs_path)
        uploaded_file = client.files.upload(file=abs_path)
        uploaded_file = wait_for_file_processing(client, uploaded_file)
        shelf[abs_path] = {
            "name": uploaded_file.name,
            "timestamp": time.time(),
        }
        return uploaded_file


def wait_for_file_processing(client: genai.Client, uploaded_file: File) -> File:
    while uploaded_file.state and uploaded_file.state.name == "PROCESSING":
        logger.info("Waiting for Gemini to finish processing %s", uploaded_file.name)
        time.sleep(2)
        uploaded_file = client.files.get(name=uploaded_file.name)

    if uploaded_file.state and uploaded_file.state.name == "FAILED":
        raise RuntimeError(f"Gemini failed to process uploaded file {uploaded_file.name}")
    return uploaded_file


def analyze_pdf(
    client: genai.Client, uploaded_file: File, *, model: str
) -> tuple[AnalysisResult, types.GenerateContentResponse]:
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_text(text=PROMPT),
            uploaded_file,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=AnalysisResult.model_json_schema(),
        ),
    )
    parsed = AnalysisResult.model_validate_json(response.text)
    return parsed, response


def print_result(entry: ArchiveEntry) -> None:
    page_text = entry.page_count if entry.page_count is not None else "?"
    print(f"Filename: {entry.filename} Pages: {page_text} Score: {entry.data.score}")
    for conn in entry.data.connections:
        print(f"- {conn.who}: {conn.what} ({conn.time_period}) ({conn.page_numbers})")

    if entry.usage:
        print(f"Prompt Tokens: {entry.usage.prompt_token_count}")
        print(f"Candidates Tokens: {entry.usage.candidates_token_count}")
        print(f"Total Tokens: {entry.usage.total_token_count}")
    print(f"Total Time: {entry.processing_time:.2f}")


def append_cost_csv(path: Path, usage: GenerateContentResponseUsageMetadata | None) -> None:
    if usage is None:
        return

    file_exists = path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if not file_exists:
            writer.writerow(["prompt_tokens", "candidate_tokens", "total_tokens"])
        writer.writerow(
            [
                usage.prompt_token_count,
                usage.candidates_token_count,
                usage.total_token_count,
            ]
        )


def append_jsonl(path: Path, entry: ArchiveEntry) -> None:
    with path.open("a") as handle:
        handle.write(entry.model_dump_json())
        handle.write("\n")


def main() -> int:
    args = parse_args()
    source_pdf = ensure_pdf_exists(Path(args.pdf))
    inspector = PDFInspector(source_pdf)
    page_count = inspector.page_count
    if should_skip_pdf(source_pdf, inspector):
        return 0

    api_key = require_api_key()
    client = build_client(api_key)

    if args.list_server_files:
        list_files_on_server(client)

    started = time.time()
    uploaded_file = get_cached_file(client, source_pdf, shelf_name=args.cache)
    logger.info("Using Gemini file URI %s", uploaded_file.uri)
    analysis, response = analyze_pdf(client, uploaded_file, model=args.model)
    elapsed = time.time() - started

    entry = ArchiveEntry(
        filename=source_pdf.name,
        page_count=page_count,
        timestamp=time.time(),
        processing_time=elapsed,
        data=analysis,
        usage=response.usage_metadata,
        model_version=response.model_version or args.model,
        uploaded_filename=uploaded_file.name or source_pdf.name,
        uploaded_bytes=source_pdf.stat().st_size,
    )

    print_result(entry)
    append_cost_csv(Path(args.cost_csv), entry.usage)
    append_jsonl(Path(args.output_jsonl), entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
