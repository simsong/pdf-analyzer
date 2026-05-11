# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

import json
from typing import Any

from .gemini import (
    DOCUMENT_CHUNK_TEMPLATE,
    DOCUMENT_PROMPT_INSTRUCTIONS,
    DOCUMENT_SPLIT_NOTE,
    SYNTHESIS_INSTRUCTIONS,
    SYNTHESIS_PROMPT_PAYLOAD_KEYS,
)
from .models import DocumentAnalysisResult, ProjectSynthesisResult
from .utils import bytes_sha256


def _fingerprint_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return bytes_sha256(serialized.encode("utf-8"))


def _short_fingerprint(value: str, length: int = 12) -> str:
    return value[:length]


def build_query_identity(
    *,
    prompt_version: str,
    schema_version: str,
    synthesis_prompt_version: str,
) -> dict[str, str]:
    document_prompt_fingerprint = _fingerprint_payload(
        {
            "builder": "build_document_prompt",
            "instructions": DOCUMENT_PROMPT_INSTRUCTIONS,
            "split_note": DOCUMENT_SPLIT_NOTE,
            "chunk_template": DOCUMENT_CHUNK_TEMPLATE,
        }
    )
    document_schema_fingerprint = _fingerprint_payload(
        DocumentAnalysisResult.model_json_schema()
    )
    synthesis_prompt_fingerprint = _fingerprint_payload(
        {
            "builder": "build_synthesis_prompt",
            "instructions": SYNTHESIS_INSTRUCTIONS,
            "payload_keys": SYNTHESIS_PROMPT_PAYLOAD_KEYS,
        }
    )
    synthesis_schema_fingerprint = _fingerprint_payload(
        ProjectSynthesisResult.model_json_schema()
    )
    return {
        "configured_prompt_version": prompt_version,
        "configured_schema_version": schema_version,
        "configured_synthesis_prompt_version": synthesis_prompt_version,
        "document_prompt_fingerprint": document_prompt_fingerprint,
        "document_schema_fingerprint": document_schema_fingerprint,
        "synthesis_prompt_fingerprint": synthesis_prompt_fingerprint,
        "synthesis_schema_fingerprint": synthesis_schema_fingerprint,
        "effective_prompt_version": (
            f"{prompt_version}+pf:{_short_fingerprint(document_prompt_fingerprint)}"
        ),
        "effective_schema_version": (
            f"{schema_version}+sf:{_short_fingerprint(document_schema_fingerprint)}"
        ),
        "effective_synthesis_prompt_version": (
            f"{synthesis_prompt_version}"
            f"+spf:{_short_fingerprint(synthesis_prompt_fingerprint)}"
            f"+ssf:{_short_fingerprint(synthesis_schema_fingerprint)}"
        ),
    }
