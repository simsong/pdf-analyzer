import argparse
import csv
import logging
import os
import shelve
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors, types
from google.genai.types import File, GenerateContentResponseUsageMetadata
from pydantic import BaseModel, Field
from .pdf_tools import (
    DEFAULT_JPEG_QUALITY,
    PDF_SIZE_LIMIT_BYTES,
    PDFInspector,
    CompressionCandidate,
    choose_pdf_candidates,
    ensure_pdf_exists,
    human_size,
)
from .pricing import estimate_usage_cost, get_model_pricing


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
    analyzed_filename: str
    page_count: int | None = None
    timestamp: float
    processing_time: float
    data: AnalysisResult
    usage: GenerateContentResponseUsageMetadata | None = None
    model_version: str
    uploaded_filename: str
    analyzed_bytes: int
    compression_method: str | None = None
    cost: dict[str, Any] | None = None


class ChunkManifestEntry(BaseModel):
    path_name: str
    size_bytes: int
    start_page: int | None = None
    end_page: int | None = None
    remote_name: str
    uri: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze MIT PDFs for Louisiana connections via Gemini."
    )
    parser.add_argument("pdfs", nargs="+", help="Path(s) to PDF files to analyze")
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
    parser.add_argument(
        "--oversize-strategy",
        choices=["chunk", "auto", "none", "qpdf", "ebook"],
        default="chunk",
        help="How to prepare oversized PDFs before sending them to Gemini.",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=DEFAULT_JPEG_QUALITY,
        help="JPEG quality used by qpdf image optimization.",
    )
    return parser.parse_args()


def require_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set. Export it before running analyze-pdfs.")
    return api_key


def build_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def get_pricing_snapshot(model_name: str) -> dict[str, Any]:
    pricing = get_model_pricing(model_name)
    return {
        "model_name": pricing.model_name,
        "input_usd_per_million_tokens": pricing.input_usd_per_million_tokens,
        "output_usd_per_million_tokens": pricing.output_usd_per_million_tokens,
    }


def list_files_on_server(client: genai.Client) -> None:
    print("Currently active files on server:")
    for remote_file in client.files.list():
        print(
            f"- {remote_file.display_name} ({remote_file.name}) | "
            f"Expires: {remote_file.expiration_time}"
        )


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
            "uri": uploaded_file.uri,
            "timestamp": time.time(),
        }
        return uploaded_file


def source_cache_signature(source_pdf: Path, inspector: PDFInspector) -> dict[str, Any]:
    stat = source_pdf.stat()
    return {
        "path": str(source_pdf.resolve()),
        "size_bytes": inspector.file_size,
        "mtime_ns": stat.st_mtime_ns,
        "page_count": inspector.page_count,
    }


def chunk_manifest_key(source_pdf: Path) -> str:
    return f"chunk_manifest::{source_pdf.resolve()}"


def get_remote_file_if_available(
    client: genai.Client,
    *,
    remote_name: str,
    expected_uri: str | None = None,
) -> File | None:
    try:
        remote_file = client.files.get(name=remote_name)
    except errors.ClientError:
        return None

    remote_file = wait_for_file_processing(client, remote_file)
    if expected_uri and remote_file.uri and remote_file.uri != expected_uri:
        logger.info(
            "Gemini file URI changed for %s; updating cached manifest.",
            remote_name,
        )
    return remote_file


def load_cached_chunk_manifest(
    client: genai.Client,
    source_pdf: Path,
    inspector: PDFInspector,
    *,
    shelf_name: str,
) -> tuple[list[CompressionCandidate], list[File]] | None:
    manifest_key = chunk_manifest_key(source_pdf)
    signature = source_cache_signature(source_pdf, inspector)

    with shelve.open(shelf_name, writeback=True) as shelf:
        manifest = shelf.get(manifest_key)
        if manifest is None:
            return None

        if time.time() - manifest.get("timestamp", 0) >= CACHE_TTL_SECONDS:
            logger.info("Cached chunk manifest expired for %s.", source_pdf.name)
            shelf.pop(manifest_key, None)
            return None

        if manifest.get("source") != signature:
            logger.info("Cached chunk manifest no longer matches %s.", source_pdf.name)
            shelf.pop(manifest_key, None)
            return None

        cached_chunks = manifest.get("chunks") or []
        if not cached_chunks:
            shelf.pop(manifest_key, None)
            return None

        submitted_pdfs: list[CompressionCandidate] = []
        uploaded_files: list[File] = []
        refreshed_chunks: list[dict[str, Any]] = []

        for chunk in cached_chunks:
            remote_name = chunk.get("remote_name")
            if not remote_name:
                shelf.pop(manifest_key, None)
                return None

            uploaded_file = get_remote_file_if_available(
                client,
                remote_name=remote_name,
                expected_uri=chunk.get("uri"),
            )
            if uploaded_file is None or not uploaded_file.uri:
                logger.info(
                    "Cached chunk %s is no longer available on Gemini. Rebuilding manifest.",
                    remote_name,
                )
                shelf.pop(manifest_key, None)
                return None

            submitted_pdfs.append(
                CompressionCandidate(
                    method="chunk",
                    path=source_pdf.with_name(chunk["path_name"]),
                    size_bytes=chunk["size_bytes"],
                    start_page=chunk.get("start_page"),
                    end_page=chunk.get("end_page"),
                )
            )
            uploaded_files.append(uploaded_file)
            refreshed_chunks.append(
                {
                    **chunk,
                    "uri": uploaded_file.uri,
                    "remote_name": uploaded_file.name,
                }
            )

        manifest["timestamp"] = time.time()
        manifest["chunks"] = refreshed_chunks
        shelf[manifest_key] = manifest
        logger.info("Reusing cached chunk manifest for %s.", source_pdf.name)
        return submitted_pdfs, uploaded_files


def store_chunk_manifest(
    source_pdf: Path,
    inspector: PDFInspector,
    submitted_pdfs: list[CompressionCandidate],
    uploaded_files: list[File],
    *,
    shelf_name: str,
) -> None:
    manifest_key = chunk_manifest_key(source_pdf)
    chunks = [
        ChunkManifestEntry(
            path_name=submitted_pdf.path.name,
            size_bytes=submitted_pdf.size_bytes,
            start_page=submitted_pdf.start_page,
            end_page=submitted_pdf.end_page,
            remote_name=uploaded_file.name or "",
            uri=uploaded_file.uri,
        ).model_dump()
        for submitted_pdf, uploaded_file in zip(submitted_pdfs, uploaded_files, strict=True)
    ]
    with shelve.open(shelf_name, writeback=True) as shelf:
        shelf[manifest_key] = {
            "timestamp": time.time(),
            "source": source_cache_signature(source_pdf, inspector),
            "chunks": chunks,
        }


def delete_local_chunk_files(source_pdf: Path, submitted_pdfs: list[CompressionCandidate]) -> None:
    for submitted_pdf in submitted_pdfs:
        if submitted_pdf.method != "chunk":
            continue
        if submitted_pdf.path.resolve() == source_pdf.resolve():
            continue
        if submitted_pdf.path.exists():
            submitted_pdf.path.unlink()
            logger.info("Deleted local chunk file %s", submitted_pdf.path)


def prepare_uploaded_pdfs(
    client: genai.Client,
    source_pdf: Path,
    inspector: PDFInspector,
    *,
    oversize_strategy: str,
    jpeg_quality: int,
    cache_path: str,
) -> tuple[list[CompressionCandidate], list[File]]:
    if oversize_strategy in {"chunk", "auto"} and inspector.file_size > PDF_SIZE_LIMIT_BYTES:
        cached = load_cached_chunk_manifest(
            client,
            source_pdf,
            inspector,
            shelf_name=cache_path,
        )
        if cached is not None:
            return cached

    submitted_pdfs = choose_pdf_candidates(
        source_pdf,
        inspector,
        oversize_strategy=oversize_strategy,
        jpeg_quality=jpeg_quality,
    )
    if not submitted_pdfs:
        return [], []

    uploaded_files: list[File] = []
    for submitted_pdf in submitted_pdfs:
        page_label = (
            f"pages {submitted_pdf.start_page}-{submitted_pdf.end_page}"
            if submitted_pdf.start_page is not None and submitted_pdf.end_page is not None
            else submitted_pdf.path.name
        )
        logger.info("Uploading %s (%s)", submitted_pdf.path.name, page_label)
        uploaded_files.append(
            get_cached_file(client, submitted_pdf.path, shelf_name=cache_path)
        )

    if submitted_pdfs and all(candidate.method == "chunk" for candidate in submitted_pdfs):
        store_chunk_manifest(
            source_pdf,
            inspector,
            submitted_pdfs,
            uploaded_files,
            shelf_name=cache_path,
        )
        delete_local_chunk_files(source_pdf, submitted_pdfs)

    return submitted_pdfs, uploaded_files


def wait_for_file_processing(client: genai.Client, uploaded_file: File) -> File:
    while uploaded_file.state and uploaded_file.state.name == "PROCESSING":
        logger.info("Waiting for Gemini to finish processing %s", uploaded_file.name)
        time.sleep(2)
        uploaded_file = client.files.get(name=uploaded_file.name)

    if uploaded_file.state and uploaded_file.state.name == "FAILED":
        raise RuntimeError(f"Gemini failed to process uploaded file {uploaded_file.name}")
    return uploaded_file


def analyze_pdf(
    client: genai.Client, uploaded_files: list[File], *, model: str, prompt: str
) -> tuple[AnalysisResult, types.GenerateContentResponse]:
    contents: list[types.Part | File] = [types.Part.from_text(text=prompt), *uploaded_files]
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=AnalysisResult.model_json_schema(),
        ),
    )
    parsed = AnalysisResult.model_validate_json(response.text)
    return parsed, response


def build_analysis_prompt(source_pdf: Path, submitted_pdfs: list[CompressionCandidate]) -> str:
    if len(submitted_pdfs) == 1:
        return PROMPT

    chunk_lines = []
    for index, candidate in enumerate(submitted_pdfs, start=1):
        if candidate.start_page is not None and candidate.end_page is not None:
            chunk_lines.append(
                f"Chunk {index}: file `{candidate.path.name}` contains original pages "
                f"{candidate.start_page}-{candidate.end_page}."
            )
        else:
            chunk_lines.append(f"Chunk {index}: file `{candidate.path.name}`.")

    chunk_map = "\n".join(chunk_lines)
    return (
        f"{PROMPT}\n\n"
        f"The original document `{source_pdf.name}` has been split into multiple PDF chunks "
        f"because of file-size limits. Treat all chunks as one continuous document.\n"
        f"{chunk_map}\n\n"
        "Return page_numbers using the original document page numbers, not chunk-local page numbers."
    )


def print_result(entry: ArchiveEntry) -> None:
    page_text = entry.page_count if entry.page_count is not None else "?"
    print(f"Filename: {entry.filename} Pages: {page_text} Score: {entry.data.score}")
    if entry.analyzed_filename != entry.filename:
        print(
            f"Submitted PDF: {entry.analyzed_filename} "
            f"({human_size(entry.analyzed_bytes)}) via {entry.compression_method}"
        )
    for conn in entry.data.connections:
        print(f"- {conn.who}: {conn.what} ({conn.time_period}) ({conn.page_numbers})")

    if entry.usage:
        print(f"Prompt Tokens: {entry.usage.prompt_token_count}")
        print(f"Candidates Tokens: {entry.usage.candidates_token_count}")
        print(f"Total Tokens: {entry.usage.total_token_count}")
    if entry.cost:
        print(
            "Estimated Cost: "
            f"${entry.cost['total_cost_usd']:.4f} "
            f"(input=${entry.cost['input_cost_usd']:.4f}, "
            f"output=${entry.cost['output_cost_usd']:.4f})"
        )
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


def process_pdf(
    client: genai.Client | None,
    source_pdf: Path,
    *,
    submitted_pdfs: list[CompressionCandidate],
    uploaded_files: list[File],
    inspector: PDFInspector,
    model: str,
    started_at: float,
    pricing_snapshot: dict[str, Any],
    cost_csv: Path,
    output_jsonl: Path,
) -> int:
    page_count = inspector.page_count
    if client is None:
        raise SystemExit(
            "GEMINI_API_KEY is not set. Export it before analyzing PDFs that are within the size limit."
        )

    analyzed_names: list[str] = []
    total_analyzed_bytes = 0
    uploaded_names: list[str] = []

    for submitted_pdf, uploaded_file in zip(submitted_pdfs, uploaded_files, strict=True):
        analyzed_names.append(submitted_pdf.path.name)
        total_analyzed_bytes += submitted_pdf.size_bytes
        uploaded_names.append(uploaded_file.name or submitted_pdf.path.name)

    for uploaded_file in uploaded_files:
        logger.info("Using Gemini file URI %s", uploaded_file.uri)

    prompt = build_analysis_prompt(source_pdf, submitted_pdfs)
    analysis, response = analyze_pdf(client, uploaded_files, model=model, prompt=prompt)
    elapsed = time.time() - started_at
    usage = response.usage_metadata
    cost = None
    if usage is not None:
        cost_estimate = estimate_usage_cost(
            pricing_snapshot["model_name"],
            prompt_tokens=usage.prompt_token_count or 0,
            candidate_tokens=usage.candidates_token_count or 0,
        )
        cost = {
            "pricing_model_name": pricing_snapshot["model_name"],
            "input_usd_per_million_tokens": pricing_snapshot["input_usd_per_million_tokens"],
            "output_usd_per_million_tokens": pricing_snapshot["output_usd_per_million_tokens"],
            "input_cost_usd": cost_estimate.input_cost_usd,
            "output_cost_usd": cost_estimate.output_cost_usd,
            "total_cost_usd": cost_estimate.total_cost_usd,
        }

    if len(submitted_pdfs) == 1:
        analyzed_filename = analyzed_names[0]
        uploaded_filename = uploaded_names[0]
        compression_method = (
            None if submitted_pdfs[0].method == "original" else submitted_pdfs[0].method
        )
        analyzed_bytes = submitted_pdfs[0].size_bytes
    else:
        analyzed_filename = f"{source_pdf.stem}_chunk_*.pdf ({len(submitted_pdfs)} chunks)"
        uploaded_filename = ", ".join(uploaded_names)
        compression_method = "chunk"
        analyzed_bytes = total_analyzed_bytes

    entry = ArchiveEntry(
        filename=source_pdf.name,
        analyzed_filename=analyzed_filename,
        page_count=page_count,
        timestamp=time.time(),
        processing_time=elapsed,
        data=analysis,
        usage=usage,
        model_version=response.model_version or model,
        uploaded_filename=uploaded_filename,
        analyzed_bytes=analyzed_bytes,
        compression_method=compression_method,
        cost=cost,
    )

    print_result(entry)
    append_cost_csv(cost_csv, entry.usage)
    append_jsonl(output_jsonl, entry)
    return 0


def main() -> int:
    args = parse_args()
    client: genai.Client | None = None

    if args.list_server_files:
        api_key = require_api_key()
        client = build_client(api_key)
        list_files_on_server(client)

    pricing_snapshot = get_pricing_snapshot(args.model)

    cost_csv = Path(args.cost_csv)
    output_jsonl = Path(args.output_jsonl)
    exit_code = 0
    for pdf in args.pdfs:
        source_pdf = ensure_pdf_exists(Path(pdf))
        inspector = PDFInspector(source_pdf)
        if client is None:
            api_key = require_api_key()
            client = build_client(api_key)
        started_at = time.time()
        submission_pdfs, uploaded_files = prepare_uploaded_pdfs(
            client,
            source_pdf,
            inspector,
            oversize_strategy=args.oversize_strategy,
            jpeg_quality=args.jpeg_quality,
            cache_path=args.cache,
        )
        if not submission_pdfs:
            continue
        exit_code = max(
            exit_code,
            process_pdf(
                client,
                source_pdf,
                submitted_pdfs=submission_pdfs,
                uploaded_files=uploaded_files,
                inspector=inspector,
                model=args.model,
                started_at=started_at,
                pricing_snapshot=pricing_snapshot,
                cost_csv=cost_csv,
                output_jsonl=output_jsonl,
            ),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
