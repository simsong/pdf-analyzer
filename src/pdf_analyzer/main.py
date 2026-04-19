import argparse
import concurrent.futures
import json
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .constants import (
    DEFAULT_DATABASE_NAME,
    DEFAULT_MODEL,
    DEFAULT_OVERSIZE_STRATEGY,
    DEFAULT_WORKERS,
)
from .db import Database
from .gemini import (
    analyze_document_with_files,
    build_client,
    get_or_upload_candidate,
    synthesize_project,
    usage_to_cost_fields,
)
from .pdf_tools import PDFInspector, prepare_candidates_for_upload
from .pricing import (
    DEFAULT_PRICING_FETCHER,
    PRICING_OBJECT_NAME,
    fetch_pricing_snapshot,
    get_model_pricing,
)
from .reporting import generate_reports
from .utils import file_hashes, normalize_question, utc_now_iso

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a PDF archive for a configured question using Gemini and SQLite.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("config", nargs="?", type=Path, help="Path to the YAML project config.")
    parser.add_argument("--model", default=None, help="Override the model configured in YAML.")
    parser.add_argument("--workers", type=int, default=None, help="Number of worker threads.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of PDFs to analyze this run.")
    parser.add_argument("--force", action="store_true", help="Re-run analyses even if successful results already exist.")
    parser.add_argument("--no-gemini", action="store_true", help="Do not make new Gemini API calls; just refresh the report from SQLite state.")
    parser.add_argument("--list-models", action="store_true", help="List available Gemini models and the latest retrievable pricing, then exit.")
    parser.add_argument("--oversize-strategy", default=None, choices=["chunk", "auto", "none", "qpdf", "ebook"], help="Override the configured oversized-PDF strategy.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    args = parser.parse_args()
    if not args.list_models and args.config is None:
        parser.error("config is required unless --list-models is used")
    return args


def _format_price(value: float | None) -> str:
    if value is None:
        return "?"
    return f"${value:.2f}"


def _format_limit(value: Any) -> str:
    if not value:
        return "?"
    return f"{int(value):,}"


def list_models_with_pricing() -> None:
    client = build_client()
    pricing_snapshot: dict[str, Any] | None = None
    try:
        pricing_snapshot, _ = fetch_pricing_snapshot()
    except Exception as exc:
        LOGGER.warning("Could not fetch Gemini pricing snapshot: %s", exc)
    all_models = sorted(
        list(client.models.list()),
        key=lambda model: getattr(model, "name", ""),
    )
    print(
        f"{'MODEL ID':<40} | {'INPUT $/1M':<10} | {'OUTPUT $/1M':<11} | "
        f"{'INPUT LIMIT':<12} | {'OUTPUT LIMIT':<12}"
    )
    print("-" * 97)
    for model in all_models:
        raw_name = getattr(model, "name", "Unknown")
        model_name = raw_name.replace("models/", "")
        try:
            pricing = get_model_pricing(pricing_snapshot or {}, model_name)
            input_price = pricing.input_usd_per_million_tokens
            output_price = pricing.output_usd_per_million_tokens
        except ValueError:
            input_price = None
            output_price = None
        print(
            f"{model_name:<40} | {_format_price(input_price):<10} | "
            f"{_format_price(output_price):<11} | "
            f"{_format_limit(getattr(model, 'input_token_limit', None)):<12} | "
            f"{_format_limit(getattr(model, 'output_token_limit', None)):<12}"
        )


def maybe_store_pricing_snapshot(
    *,
    db: Database,
    refresh: bool,
) -> dict[str, Any] | None:
    latest = db.fetch_latest_object(PRICING_OBJECT_NAME)
    if not refresh:
        if latest is None:
            return None
        return json.loads(latest["json_object"])

    try:
        payload, metadata = fetch_pricing_snapshot()
        db.insert_object(
            object_name=PRICING_OBJECT_NAME,
            stored_at=utc_now_iso(),
            source_url=metadata["source_url"],
            fetched_by=DEFAULT_PRICING_FETCHER,
            json_object=payload,
            content_sha256=metadata.get("content_sha256"),
            content_type=metadata.get("content_type"),
            source_etag=metadata.get("source_etag"),
            source_last_modified=metadata.get("source_last_modified"),
        )
        return payload
    except Exception as exc:
        LOGGER.warning("Could not refresh Gemini pricing snapshot: %s", exc)
        if latest is None:
            return None
        return json.loads(latest["json_object"])


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    # Suppress known third-party noise that obscures archive-analysis output.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("pypdf._reader").setLevel(logging.ERROR)
    logging.getLogger("google_genai.models").setLevel(logging.WARNING)


def discover_pdf_paths(pdf_directory: Path) -> list[Path]:
    return sorted(path for path in pdf_directory.rglob("*.pdf") if path.is_file())


def scan_archive(db: Database, config: ProjectConfig) -> list[dict[str, Any]]:
    now_iso = utc_now_iso()
    discovered: list[dict[str, Any]] = []
    for pdf_path in discover_pdf_paths(config.resolved_pdf_directory):
        try:
            sha256, _, size_bytes = file_hashes(pdf_path)
            inspector = PDFInspector(pdf_path)
        except Exception as exc:
            LOGGER.warning("Skipping unreadable PDF %s: %s", pdf_path, exc)
            continue
        relative_path = str(pdf_path.relative_to(config.resolved_pdf_directory))
        db.upsert_document(
            sha256=sha256,
            file_size_bytes=size_bytes,
            page_count=inspector.page_count,
            canonical_filename=pdf_path.name,
            now_iso=now_iso,
        )
        db.upsert_document_path(
            sha256=sha256,
            absolute_path=str(pdf_path.resolve()),
            relative_path=relative_path,
            original_filename=pdf_path.name,
            now_iso=now_iso,
        )
        discovered.append(
            {
                "sha256": sha256,
                "path": pdf_path.resolve(),
                "filename": pdf_path.name,
                "relative_path": relative_path,
                "file_size_bytes": size_bytes,
                "page_count": inspector.page_count,
            }
        )
    return discovered


def process_document_task(
    *,
    database_path: Path,
    prepared_dir: Path,
    pricing_snapshot: dict[str, Any] | None,
    query_id: int,
    run_id: int,
    document_sha256: str,
    model_name: str,
    question: str,
    oversize_strategy: str,
) -> dict[str, Any]:
    db = Database(database_path)
    started_at = utc_now_iso()
    document = db.fetch_document(document_sha256)
    if document is None:
        return {"status": "failed", "failure_type": "missing_document_state"}

    source_path_value = document["preferred_path"]
    source_path = Path(source_path_value) if source_path_value else None
    if source_path is None or not source_path.exists():
        db.upsert_document_analysis(
            {
                "query_id": query_id,
                "document_sha256": document_sha256,
                "run_id": run_id,
                "status": "failed",
                "failure_type": "missing_pdf",
                "error_text": "The source PDF path no longer exists on disk.",
                "source_path": source_path_value or "",
                "source_filename": document["canonical_filename"],
                "page_count": document["page_count"],
                "model_name": model_name,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
            }
        )
        db.close()
        return {"status": "failed", "failure_type": "missing_pdf"}

    try:
        inspector, candidates = prepare_candidates_for_upload(
            source_path,
            document_sha256=document_sha256,
            work_dir=prepared_dir,
            oversize_strategy=oversize_strategy,
        )
    except Exception as exc:
        db.upsert_document_analysis(
            {
                "query_id": query_id,
                "document_sha256": document_sha256,
                "run_id": run_id,
                "status": "failed",
                "failure_type": "preparation_failed",
                "error_text": str(exc),
                "source_path": str(source_path),
                "source_filename": document["canonical_filename"],
                "page_count": document["page_count"],
                "model_name": model_name,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
            }
        )
        db.close()
        return {"status": "failed", "failure_type": "preparation_failed"}

    if not candidates:
        db.upsert_document_analysis(
            {
                "query_id": query_id,
                "document_sha256": document_sha256,
                "run_id": run_id,
                "status": "failed",
                "failure_type": "preparation_failed",
                "error_text": "No uploadable candidates were produced for this PDF.",
                "source_path": str(source_path),
                "source_filename": document["canonical_filename"],
                "page_count": inspector.page_count,
                "model_name": model_name,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
            }
        )
        db.close()
        return {"status": "failed", "failure_type": "preparation_failed"}

    client = build_client()
    uploaded_files = []
    try:
        for candidate in candidates:
            uploaded_files.append(
                get_or_upload_candidate(
                    client=client,
                    db=db,
                    run_id=run_id,
                    query_id=query_id,
                    candidate=candidate,
                )
            )
    except Exception as exc:
        db.upsert_document_analysis(
            {
                "query_id": query_id,
                "document_sha256": document_sha256,
                "run_id": run_id,
                "status": "failed",
                "failure_type": "upload_failed",
                "error_text": str(exc),
                "source_path": str(source_path),
                "source_filename": document["canonical_filename"],
                "page_count": inspector.page_count,
                "model_name": model_name,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
            }
        )
        db.close()
        return {"status": "failed", "failure_type": "upload_failed"}

    analysis_started = time.perf_counter()
    prompt_payload: dict[str, Any] | None = None
    try:
        result, prompt_payload, response = analyze_document_with_files(
            client=client,
            model_name=model_name,
            question=question,
            source_filename=document["canonical_filename"],
            candidates=candidates,
            uploaded_files=uploaded_files,
        )
        usage_fields = usage_to_cost_fields(pricing_snapshot, model_name, response.usage_metadata)
        raw_response = json.loads(response.text)
        db.insert_gemini_call_log(
            {
                "run_id": run_id,
                "query_id": query_id,
                "document_sha256": document_sha256,
                "call_type": "document_analysis",
                "status": "succeeded",
                "prompt": prompt_payload,
                "response": raw_response,
                "prompt_tokens": usage_fields["prompt_tokens"],
                "candidate_tokens": usage_fields["candidate_tokens"],
                "total_tokens": usage_fields["total_tokens"],
                "input_cost_usd": usage_fields["input_cost_usd"],
                "output_cost_usd": usage_fields["output_cost_usd"],
                "total_cost_usd": usage_fields["total_cost_usd"],
                "duration_ms": round((time.perf_counter() - analysis_started) * 1000, 1),
                "created_at": utc_now_iso(),
            }
        )
        compression_method = (
            "chunk" if len(candidates) > 1 else candidates[0].method
        )
        analysis_id = db.upsert_document_analysis(
            {
                "query_id": query_id,
                "document_sha256": document_sha256,
                "run_id": run_id,
                "status": "succeeded",
                "source_path": str(source_path),
                "source_filename": document["canonical_filename"],
                "page_count": inspector.page_count,
                "responsive": int(result.responsive),
                "relevance_score": result.relevance_score,
                "summary": result.summary,
                "people": result.people,
                "places": result.places,
                "dates": result.dates,
                "evidence_count": len(result.evidence_items),
                "raw_response": raw_response,
                "prompt": prompt_payload,
                "model_name": model_name,
                "analyzed_bytes": sum(candidate.size_bytes for candidate in candidates),
                "compression_method": compression_method,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
                **usage_fields,
            }
        )
        db.replace_analysis_evidence(
            analysis_id,
            [item.model_dump() for item in result.evidence_items],
        )
        db.close()
        return {"status": "succeeded", "responsive": result.responsive}
    except Exception as exc:
        db.insert_gemini_call_log(
            {
                "run_id": run_id,
                "query_id": query_id,
                "document_sha256": document_sha256,
                "call_type": "document_analysis",
                "status": "failed",
                "error_text": str(exc),
                "prompt": prompt_payload,
                "duration_ms": round((time.perf_counter() - analysis_started) * 1000, 1),
                "created_at": utc_now_iso(),
            }
        )
        db.upsert_document_analysis(
            {
                "query_id": query_id,
                "document_sha256": document_sha256,
                "run_id": run_id,
                "status": "failed",
                "failure_type": "analysis_failed",
                "error_text": str(exc),
                "source_path": str(source_path),
                "source_filename": document["canonical_filename"],
                "page_count": inspector.page_count,
                "prompt": prompt_payload,
                "model_name": model_name,
                "analyzed_bytes": sum(candidate.size_bytes for candidate in candidates),
                "compression_method": "chunk" if len(candidates) > 1 else candidates[0].method,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
            }
        )
        db.close()
        return {"status": "failed", "failure_type": "analysis_failed"}


def build_synthesis_documents(db: Database, query_id: int) -> tuple[list[dict[str, Any]], int]:
    document_rows = [dict(row) for row in db.report_document_rows(query_id)]
    evidence_rows = [dict(row) for row in db.report_evidence_rows(query_id)]
    by_sha: dict[str, list[dict[str, Any]]] = {}
    for row in evidence_rows:
        by_sha.setdefault(row["document_sha256"], []).append(
            {
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "summary": row["evidence_summary"],
                "people": json.loads(row["people_json"] or "[]"),
                "places": json.loads(row["places_json"] or "[]"),
                "dates": json.loads(row["dates_json"] or "[]"),
            }
        )

    successful = [row for row in document_rows if row.get("status") == "succeeded"]
    responsive_documents: list[dict[str, Any]] = []
    for row in successful:
        if row.get("responsive") != 1:
            continue
        responsive_documents.append(
            {
                "source_filename": row["canonical_filename"],
                "summary": row.get("summary") or "",
                "relevance_score": row.get("relevance_score"),
                "people": json.loads(row.get("people_json") or "[]"),
                "places": json.loads(row.get("places_json") or "[]"),
                "dates": json.loads(row.get("dates_json") or "[]"),
                "evidence_items": by_sha.get(row["sha256"], []),
            }
        )
    return responsive_documents, len(successful)


def should_refresh_synthesis(
    *,
    args: argparse.Namespace,
    pending_documents: list[dict[str, Any]],
    existing_synthesis: Any,
) -> bool:
    if args.no_gemini:
        return False
    if args.force:
        return True
    if pending_documents:
        return True
    if existing_synthesis is None:
        return True
    return existing_synthesis["status"] != "succeeded"


def maybe_skip_documents_for_no_gemini(
    *,
    db: Database,
    query_id: int,
    run_id: int,
    pending_documents: list[dict[str, Any]],
    model_name: str,
) -> int:
    skipped = 0
    for document in pending_documents:
        db.upsert_document_analysis(
            {
                "query_id": query_id,
                "document_sha256": document["sha256"],
                "run_id": run_id,
                "status": "skipped_no_gemini",
                "failure_type": "no_gemini",
                "error_text": "Skipped because --no-gemini was enabled for this run.",
                "source_path": str(document["path"]),
                "source_filename": document["filename"],
                "page_count": document["page_count"],
                "model_name": model_name,
                "started_at": utc_now_iso(),
                "completed_at": utc_now_iso(),
            }
        )
        skipped += 1
    return skipped


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    if args.list_models:
        list_models_with_pricing()
        return 0

    config = ProjectConfig.from_path(args.config)
    model_name = args.model or config.model or DEFAULT_MODEL
    workers = args.workers or config.workers or DEFAULT_WORKERS
    oversize_strategy = args.oversize_strategy or config.oversize_strategy or DEFAULT_OVERSIZE_STRATEGY

    output_dir = config.resolved_output_directory
    database_path = output_dir / DEFAULT_DATABASE_NAME
    if config.config_path is not None:
        copied_config_path = output_dir / config.config_path.name
        if copied_config_path.resolve() != config.config_path.resolve():
            shutil.copy2(config.config_path, copied_config_path)
    db = Database(database_path)
    db.init_schema()
    db.set_metadata("project_name", config.name)
    db.set_metadata("pdf_directory", str(config.resolved_pdf_directory))
    db.set_metadata("question", config.question)

    query_id = db.get_or_create_query(
        question=config.question,
        normalized_question=normalize_question(config.question),
        model_name=model_name,
        prompt_version=config.prompt_version,
        schema_version=config.schema_version,
        synthesis_prompt_version=config.synthesis_prompt_version,
        now_iso=utc_now_iso(),
    )
    run_id = db.start_run(
        query_id=query_id,
        config_path=str(config.config_path or args.config),
        no_gemini=args.no_gemini,
        workers=workers,
        started_at=utc_now_iso(),
    )

    try:
        with tempfile.TemporaryDirectory(prefix="pdf-analyzer-") as temporary_dir:
            prepared_dir = Path(temporary_dir) / "prepared"

            discovered_documents = scan_archive(db, config)
            LOGGER.info(
                "Scanned %s PDFs under %s",
                len(discovered_documents),
                config.resolved_pdf_directory,
            )

            pending_documents = []
            for document in discovered_documents:
                if not args.force and db.successful_analysis_exists(query_id, document["sha256"]):
                    continue
                pending_documents.append(document)
            if args.limit is not None:
                pending_documents = pending_documents[: args.limit]

            LOGGER.info("Queued %s PDFs for analysis", len(pending_documents))

            analyzed_documents = 0
            succeeded_documents = 0
            failed_documents = 0
            skipped_documents = 0
            pricing_snapshot: dict[str, Any] | None = None

            if args.no_gemini:
                skipped_documents = maybe_skip_documents_for_no_gemini(
                    db=db,
                    query_id=query_id,
                    run_id=run_id,
                    pending_documents=pending_documents,
                    model_name=model_name,
                )
                if pending_documents:
                    LOGGER.info(
                        "Skipped %s/%s queued PDFs because --no-gemini was enabled; %s remain unprocessed by Gemini for this run.",
                        skipped_documents,
                        len(pending_documents),
                        len(pending_documents) - skipped_documents,
                    )
            elif pending_documents:
                pricing_snapshot = maybe_store_pricing_snapshot(db=db, refresh=True)
                total_pending = len(pending_documents)
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [
                        executor.submit(
                            process_document_task,
                            database_path=database_path,
                            prepared_dir=prepared_dir,
                            pricing_snapshot=pricing_snapshot,
                            query_id=query_id,
                            run_id=run_id,
                            document_sha256=document["sha256"],
                            model_name=model_name,
                            question=config.question,
                            oversize_strategy=oversize_strategy,
                        )
                        for document in pending_documents
                    ]
                    for future in concurrent.futures.as_completed(futures):
                        result = future.result()
                        analyzed_documents += 1
                        if result["status"] == "succeeded":
                            succeeded_documents += 1
                        elif result["status"] == "failed":
                            failed_documents += 1
                        else:
                            skipped_documents += 1
                        remaining_documents = total_pending - analyzed_documents
                        LOGGER.info(
                            "Processed %s/%s queued PDFs; %s remaining (succeeded=%s, failed=%s, skipped=%s).",
                            analyzed_documents,
                            total_pending,
                            remaining_documents,
                            succeeded_documents,
                            failed_documents,
                            skipped_documents,
                        )

            existing_synthesis = db.fetch_synthesis(query_id)
            if should_refresh_synthesis(
                args=args,
                pending_documents=pending_documents,
                existing_synthesis=existing_synthesis,
            ):
                if pricing_snapshot is None:
                    pricing_snapshot = maybe_store_pricing_snapshot(db=db, refresh=True)
                responsive_documents, successful_documents = build_synthesis_documents(db, query_id)
                synthesis_started = utc_now_iso()
                if successful_documents == 0:
                    db.upsert_synthesis(
                        {
                            "query_id": query_id,
                            "run_id": run_id,
                            "status": "skipped",
                            "error_text": "No successful document analyses are available yet.",
                            "model_name": model_name,
                            "started_at": synthesis_started,
                            "completed_at": utc_now_iso(),
                        }
                    )
                elif not responsive_documents:
                    db.upsert_synthesis(
                        {
                            "query_id": query_id,
                            "run_id": run_id,
                            "status": "succeeded",
                            "answer": "No responsive documents were identified for the configured question in the analyzed archive.",
                            "key_findings": [],
                            "people": [],
                            "places": [],
                            "dates": [],
                            "reasoning_notes": "This synthesis was produced deterministically because none of the successful per-document analyses were marked responsive.",
                            "model_name": model_name,
                            "started_at": synthesis_started,
                            "completed_at": utc_now_iso(),
                        }
                    )
                else:
                    client = build_client()
                    started = time.perf_counter()
                    prompt_payload: dict[str, Any] | None = None
                    try:
                        synthesis, prompt_payload, response = synthesize_project(
                            client=client,
                            model_name=model_name,
                            question=config.question,
                            total_documents=len(discovered_documents),
                            successful_documents=successful_documents,
                            responsive_documents=responsive_documents,
                        )
                        usage_fields = usage_to_cost_fields(pricing_snapshot, model_name, response.usage_metadata)
                        raw_response = json.loads(response.text)
                        db.insert_gemini_call_log(
                            {
                                "run_id": run_id,
                                "query_id": query_id,
                                "call_type": "project_synthesis",
                                "status": "succeeded",
                                "prompt": prompt_payload,
                                "response": raw_response,
                                "prompt_tokens": usage_fields["prompt_tokens"],
                                "candidate_tokens": usage_fields["candidate_tokens"],
                                "total_tokens": usage_fields["total_tokens"],
                                "input_cost_usd": usage_fields["input_cost_usd"],
                                "output_cost_usd": usage_fields["output_cost_usd"],
                                "total_cost_usd": usage_fields["total_cost_usd"],
                                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                                "created_at": utc_now_iso(),
                            }
                        )
                        db.upsert_synthesis(
                            {
                                "query_id": query_id,
                                "run_id": run_id,
                                "status": "succeeded",
                                "answer": synthesis.answer,
                                "key_findings": synthesis.key_findings,
                                "people": synthesis.people,
                                "places": synthesis.places,
                                "dates": synthesis.dates,
                                "reasoning_notes": synthesis.reasoning_notes,
                                "raw_response": raw_response,
                                "prompt": prompt_payload,
                                "model_name": model_name,
                                "started_at": synthesis_started,
                                "completed_at": utc_now_iso(),
                                **usage_fields,
                            }
                        )
                    except Exception as exc:
                        db.insert_gemini_call_log(
                            {
                                "run_id": run_id,
                                "query_id": query_id,
                                "call_type": "project_synthesis",
                                "status": "failed",
                                "error_text": str(exc),
                                "prompt": prompt_payload,
                                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                                "created_at": utc_now_iso(),
                            }
                        )
                        db.upsert_synthesis(
                            {
                                "query_id": query_id,
                                "run_id": run_id,
                                "status": "failed",
                                "error_text": str(exc),
                                "prompt": prompt_payload,
                                "model_name": model_name,
                                "started_at": synthesis_started,
                                "completed_at": utc_now_iso(),
                            }
                        )
            elif existing_synthesis is not None:
                LOGGER.info(
                    "Reusing cached project synthesis for this query; no new PDFs required Gemini analysis."
                )

            upload_count = db.count_uploads_validated_this_run(run_id)
            run_summary = db.calculate_run_summary(
                run_id=run_id,
                scanned_documents=len(discovered_documents),
                queued_documents=len(pending_documents),
                analyzed_documents=analyzed_documents,
                succeeded_documents=succeeded_documents,
                failed_documents=failed_documents,
                skipped_documents=skipped_documents,
                upload_count=upload_count,
            )
            run_summary["scanned_pages"] = sum(document.get("page_count") or 0 for document in discovered_documents)

            report_html_path, report_xlsx_path = generate_reports(
                db=db,
                query_id=query_id,
                output_dir=output_dir,
                question=config.question,
                project_name=config.name,
                run_summary=run_summary,
            )
            db.finalize_run(
                run_id=run_id,
                completed_at=utc_now_iso(),
                scanned_documents=len(discovered_documents),
                queued_documents=len(pending_documents),
                analyzed_documents=analyzed_documents,
                succeeded_documents=succeeded_documents,
                failed_documents=failed_documents,
                skipped_documents=skipped_documents,
                upload_count=upload_count,
                report_html_path=str(report_html_path),
                report_xlsx_path=str(report_xlsx_path),
            )
            LOGGER.info("Wrote %s and %s", report_html_path, report_xlsx_path)
            return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
