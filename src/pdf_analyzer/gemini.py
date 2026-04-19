import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors, types
from google.genai.types import File, GenerateContentResponseUsageMetadata

from .constants import DEFAULT_MODEL, GEMINI_FILE_TTL_HOURS
from .models import DocumentAnalysisResult, PreparedCandidate, ProjectSynthesisResult
from .pricing import estimate_usage_cost
from .utils import utc_now_iso

LOGGER = logging.getLogger(__name__)


def require_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set.")
    return api_key


def build_client(api_key: str | None = None) -> genai.Client:
    return genai.Client(api_key=api_key or require_api_key())


def wait_for_file_processing(client: genai.Client, uploaded_file: File) -> File:
    while uploaded_file.state and uploaded_file.state.name == "PROCESSING":
        time.sleep(2)
        uploaded_file = client.files.get(name=uploaded_file.name)
    if uploaded_file.state and uploaded_file.state.name == "FAILED":
        raise RuntimeError(f"Gemini failed to process uploaded file {uploaded_file.name}")
    return uploaded_file


def get_or_upload_candidate(
    *,
    client: genai.Client,
    db,
    run_id: int,
    query_id: int,
    candidate: PreparedCandidate,
) -> File:
    cached = db.fetch_upload(candidate.candidate_sha256)
    if cached and cached["remote_name"]:
        try:
            remote = client.files.get(name=cached["remote_name"])
            remote = wait_for_file_processing(client, remote)
            db.upsert_upload(
                candidate_sha256=candidate.candidate_sha256,
                document_sha256=candidate.document_sha256,
                method=candidate.method,
                start_page=candidate.start_page,
                end_page=candidate.end_page,
                size_bytes=candidate.size_bytes,
                local_path=str(candidate.path),
                remote_name=remote.name,
                remote_uri=remote.uri,
                uploaded_at=cached["uploaded_at"],
                validated_at=utc_now_iso(),
                expires_at=cached["expires_at"],
            )
            return remote
        except errors.ClientError:
            LOGGER.info("Cached Gemini file expired for candidate %s", candidate.candidate_sha256)

    started = time.perf_counter()
    try:
        uploaded_file = client.files.upload(file=str(candidate.path))
        uploaded_file = wait_for_file_processing(client, uploaded_file)
        now = datetime.now(UTC)
        db.upsert_upload(
            candidate_sha256=candidate.candidate_sha256,
            document_sha256=candidate.document_sha256,
            method=candidate.method,
            start_page=candidate.start_page,
            end_page=candidate.end_page,
            size_bytes=candidate.size_bytes,
            local_path=str(candidate.path),
            remote_name=uploaded_file.name,
            remote_uri=uploaded_file.uri,
            uploaded_at=now.isoformat(),
            validated_at=now.isoformat(),
            expires_at=(now + timedelta(hours=GEMINI_FILE_TTL_HOURS)).isoformat(),
        )
        db.insert_gemini_call_log(
            {
                "run_id": run_id,
                "query_id": query_id,
                "document_sha256": candidate.document_sha256,
                "call_type": "upload",
                "status": "succeeded",
                "remote_name": uploaded_file.name,
                "remote_uri": uploaded_file.uri,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "created_at": utc_now_iso(),
            }
        )
        return uploaded_file
    except Exception as exc:
        db.insert_gemini_call_log(
            {
                "run_id": run_id,
                "query_id": query_id,
                "document_sha256": candidate.document_sha256,
                "call_type": "upload",
                "status": "failed",
                "error_text": str(exc),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "created_at": utc_now_iso(),
            }
        )
        raise


def build_document_prompt(
    *,
    question: str,
    source_filename: str,
    candidates: list[PreparedCandidate],
) -> dict[str, Any]:
    prompt_lines = [
        "You are a precise archival researcher helping a journalist answer a question from a PDF archive.",
        f"Question: {question}",
        f"Original source filename: {source_filename}",
        "Return only structured JSON matching the provided schema.",
        "Do not quote the PDF directly. Paraphrase the responsive material.",
        "If the document is not responsive, set responsive=false, keep the score low, and leave evidence_items empty.",
        "Use original document page numbers. Each evidence item must describe one responsive page or one contiguous responsive page range from the original document.",
        "Set page_start for every evidence item. Set page_end only when the evidence spans more than one page.",
        "List people, places, and dates only when they are mentioned or clearly referenced within that specific page or page range.",
        "Order each evidence item's people list by importance to that specific page or page range, most important first.",
    ]
    if len(candidates) > 1:
        prompt_lines.append(
            "This document was split into multiple PDF chunks for upload. Treat all uploaded chunks as one original document."
        )
        for index, candidate in enumerate(candidates, start=1):
            prompt_lines.append(
                f"Chunk {index}: original pages {candidate.start_page}-{candidate.end_page}."
            )
    return {"text": "\n".join(prompt_lines)}


def analyze_document_with_files(
    *,
    client: genai.Client,
    model_name: str,
    question: str,
    source_filename: str,
    candidates: list[PreparedCandidate],
    uploaded_files: list[File],
) -> tuple[DocumentAnalysisResult, dict[str, Any], types.GenerateContentResponse]:
    prompt_payload = build_document_prompt(
        question=question,
        source_filename=source_filename,
        candidates=candidates,
    )
    contents: list[types.Part | File] = [types.Part.from_text(text=prompt_payload["text"]), *uploaded_files]
    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=DocumentAnalysisResult.model_json_schema(),
        ),
    )
    return (
        DocumentAnalysisResult.model_validate_json(response.text),
        prompt_payload,
        response,
    )


def build_synthesis_prompt(
    *,
    question: str,
    total_documents: int,
    successful_documents: int,
    responsive_documents: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt = {
        "instructions": (
            "You are synthesizing previously extracted, per-document findings for a journalist. "
            "Answer the question across the archive using only the provided document summaries and evidence. "
            "Do not invent facts or quote the PDFs directly."
        ),
        "question": question,
        "total_documents": total_documents,
        "successful_documents": successful_documents,
        "responsive_documents": responsive_documents,
    }
    return prompt


def synthesize_project(
    *,
    client: genai.Client,
    model_name: str,
    question: str,
    total_documents: int,
    successful_documents: int,
    responsive_documents: list[dict[str, Any]],
) -> tuple[ProjectSynthesisResult, dict[str, Any], types.GenerateContentResponse]:
    prompt_payload = build_synthesis_prompt(
        question=question,
        total_documents=total_documents,
        successful_documents=successful_documents,
        responsive_documents=responsive_documents,
    )
    response = client.models.generate_content(
        model=model_name,
        contents=[
            types.Part.from_text(
                text=(
                    "Return only structured JSON matching the provided schema.\n\n"
                    + json.dumps(prompt_payload, indent=2, sort_keys=True)
                )
            )
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=ProjectSynthesisResult.model_json_schema(),
        ),
    )
    return (
        ProjectSynthesisResult.model_validate_json(response.text),
        prompt_payload,
        response,
    )


def usage_to_cost_fields(
    pricing_snapshot: dict[str, Any] | None,
    model_name: str,
    usage: GenerateContentResponseUsageMetadata | None,
) -> dict[str, Any]:
    if usage is None:
        return {
            "prompt_tokens": 0,
            "candidate_tokens": 0,
            "total_tokens": 0,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
        }
    estimate = estimate_usage_cost(
        pricing_snapshot,
        model_name,
        prompt_tokens=usage.prompt_token_count or 0,
        candidate_tokens=usage.candidates_token_count or 0,
        total_tokens=usage.total_token_count or 0,
    )
    if estimate is None:
        return {
            "prompt_tokens": int(usage.prompt_token_count or 0),
            "candidate_tokens": int(usage.candidates_token_count or 0),
            "total_tokens": int(usage.total_token_count or 0),
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
        }
    return {
        "prompt_tokens": estimate.prompt_tokens,
        "candidate_tokens": estimate.candidate_tokens,
        "total_tokens": estimate.total_tokens,
        "input_cost_usd": estimate.input_cost_usd,
        "output_cost_usd": estimate.output_cost_usd,
        "total_cost_usd": estimate.total_cost_usd,
    }
