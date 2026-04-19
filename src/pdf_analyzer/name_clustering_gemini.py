from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .exceptions import AnalyzerError
from .gemini import build_client
from .name_clustering_models import NameCluster, NameClusteringResult, NameStringRecord


class NameClusteringError(AnalyzerError):
    """Raised when Gemini name clustering fails."""


class _GeminiCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    representative_name_id: int
    representative_name: str
    member_name_ids: list[int] = Field(default_factory=list)
    rationale: str


class _GeminiClusterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clusters: list[_GeminiCluster] = Field(default_factory=list)


def _build_gemini_name_clustering_prompt(records: list[NameStringRecord]) -> str:
    payload = [
        {
            "id": record.id,
            "name": record.name_string,
            "mentions": record.mentions,
            "time_period": record.time_period,
            "source_filenames": list(record.source_filenames),
            "context_notes": list(record.context_notes),
        }
        for record in records
    ]
    return f"""You are clustering person-name strings into likely identity clusters.

Each input row has a unique numeric id and a name string. Your goal is to group ids that likely refer to the same historical person.

Instructions:
- Be conservative but not overly narrow.
- Account for initials, middle initials, abbreviations, and likely OCR slips.
- Use the supplied time period and source-filename context when useful.
- Every input id must appear in exactly one cluster.
- Choose the best representative name from the cluster, preferring the fullest and most authoritative form.
- Return only structured JSON matching the schema.

Input rows:
{json.dumps(payload, indent=2, sort_keys=True)}
"""


def _close_client_if_supported(client: Any) -> None:
    close_method = getattr(client, "close", None)
    if close_method is None:
        return
    close_callable = close_method
    if not isinstance(close_callable, Callable):
        return
    close_callable()


def cluster_names_with_gemini(
    records: list[NameStringRecord],
    *,
    model_name: str,
    client: Any | None = None,
) -> NameClusteringResult:
    owned_client = False
    if client is None:
        client = build_client()
        owned_client = True

    prompt = _build_gemini_name_clustering_prompt(records)
    record_by_id = {record.id: record for record in records}
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[types.Part.from_text(text=prompt)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=_GeminiClusterResponse.model_json_schema(),
            ),
        )
        response_text = response.text
        if response_text is None:
            raise NameClusteringError("Gemini returned an empty name-clustering response.")
        parsed = _GeminiClusterResponse.model_validate_json(response_text)
    except (genai_errors.APIError, ValidationError) as exc:
        raise NameClusteringError(str(exc)) from exc
    finally:
        if owned_client:
            _close_client_if_supported(client)

    seen_ids: set[int] = set()
    clusters: list[NameCluster] = []
    for cluster_index, cluster in enumerate(parsed.clusters, start=1):
        member_ids = tuple(sorted(cluster.member_name_ids))
        if not member_ids:
            raise NameClusteringError("Gemini returned an empty cluster.")
        for member_id in member_ids:
            if member_id not in record_by_id:
                raise NameClusteringError(f"Gemini returned unknown name id {member_id}.")
            if member_id in seen_ids:
                raise NameClusteringError(f"Gemini assigned name id {member_id} more than once.")
            seen_ids.add(member_id)
        clusters.append(
            NameCluster(
                cluster_id=cluster_index,
                representative_name_id=cluster.representative_name_id,
                representative_name=cluster.representative_name,
                member_name_ids=member_ids,
                member_names=tuple(record_by_id[member_id].name_string for member_id in member_ids),
                rationale=cluster.rationale,
            )
        )

    expected_ids = set(record_by_id)
    if seen_ids != expected_ids:
        missing_ids = sorted(expected_ids - seen_ids)
        raise NameClusteringError(f"Gemini omitted ids from clustering result: {missing_ids}")

    return NameClusteringResult(
        method="gemini",
        clusters=tuple(clusters),
        metadata={"model_name": model_name},
    )
