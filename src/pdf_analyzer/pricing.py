# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .exceptions import PricingSnapshotError

PRICING_OBJECT_NAME = "gemini-prices.google.com"
DEFAULT_PRICING_SOURCE_URL = "https://ai.google.dev/gemini-api/docs/pricing"
DEFAULT_PRICING_FETCHER = "pdf_analyzer.pricing.fetch_pricing_snapshot"
PRICING_MODELS_KEY = "models"
PRICING_DISPLAY_NAME_KEY = "display_name"
PRICING_ALIASES_KEY = "aliases"
PRICING_STANDARD_KEY = "standard"
PRICING_INPUT_USD_PER_MILLION_TOKENS_KEY = "input_usd_per_million_tokens"
PRICING_OUTPUT_USD_PER_MILLION_TOKENS_KEY = "output_usd_per_million_tokens"
PRICING_NOTES_KEY = "notes"
VERSIONED_MODEL_SUFFIX_RE = re.compile(r"^-\d{3,}(?:$|-)")


@dataclass(frozen=True)
class ModelPricing:
    model_name: str
    input_usd_per_million_tokens: float
    output_usd_per_million_tokens: float


class ModelPricingDisplay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str
    matched_model_name: str | None = None
    input_usd_per_million_tokens: float | None = None
    output_usd_per_million_tokens: float | None = None
    basis: str


@dataclass(frozen=True)
class UsageCostEstimate:
    model_name: str
    prompt_tokens: int
    candidate_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float


class PricingModelExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    aliases: list[str] = Field(default_factory=list)
    standard_input_usd_per_million_tokens: float
    standard_output_usd_per_million_tokens: float
    notes: str | None = None


class PricingSnapshotExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[PricingModelExtraction] = Field(default_factory=list)
    notes: str | None = None


def _build_pricing_extraction_prompt(*, source_url: str, source_html: str) -> str:
    return f"""You are extracting Gemini Developer API pricing from Google's published pricing page.
Source URL: {source_url}

Return only structured JSON matching the provided schema.
Extract only Gemini Developer API model pricing from this source HTML.
For each model, return the aliases exactly as shown when available.
For prices, return only the Standard paid-tier text/image/video rates in USD per 1M tokens.
Ignore Free tier, Batch, Flex, Priority, grounding, context caching, storage, and audio-only prices.
If a Standard paid tier has multiple prompt-length bands, return the lower-band rate and mention the threshold in notes.
Do not invent missing models or prices.

Source HTML follows:
{source_html}
"""


def extract_pricing_snapshot_with_gemini(
    *,
    client: Any,
    model_name: str,
    source_url: str,
    source_html: str,
) -> dict[str, Any]:
    prompt = _build_pricing_extraction_prompt(source_url=source_url, source_html=source_html)
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[types.Part.from_text(text=prompt)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=PricingSnapshotExtraction.model_json_schema(),
            ),
        )
        response_text = response.text
        if response_text is None:
            raise PricingSnapshotError("Gemini returned an empty pricing extraction response.")
        extraction = PricingSnapshotExtraction.model_validate_json(response_text)
    except (genai_errors.APIError, ValidationError) as exc:
        raise PricingSnapshotError(str(exc)) from exc
    models: dict[str, Any] = {}
    for item in extraction.models:
        entry = {
            PRICING_DISPLAY_NAME_KEY: item.display_name,
            PRICING_ALIASES_KEY: item.aliases,
            PRICING_STANDARD_KEY: {
                PRICING_INPUT_USD_PER_MILLION_TOKENS_KEY: item.standard_input_usd_per_million_tokens,
                PRICING_OUTPUT_USD_PER_MILLION_TOKENS_KEY: item.standard_output_usd_per_million_tokens,
            },
            PRICING_NOTES_KEY: item.notes,
        }
        for alias in item.aliases:
            models[alias] = entry

    return {
        "object_name": PRICING_OBJECT_NAME,
        "source_url": source_url,
        "pricing_mode": "standard",
        "parsed_by_model": model_name,
        "notes": extraction.notes,
        PRICING_MODELS_KEY: models,
    }


def fetch_pricing_snapshot(
    *,
    client: Any,
    model_name: str,
    source_url: str = DEFAULT_PRICING_SOURCE_URL,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = Request(
        source_url,
        headers={
            "User-Agent": "pdf-analyzer/0.1 (+https://ai.google.dev/gemini-api/docs/pricing)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            body_bytes = response.read()
            headers = dict(response.headers.items())
            content_type = response.headers.get_content_type()
        source_html = body_bytes.decode("utf-8", errors="replace")
        payload = extract_pricing_snapshot_with_gemini(
            client=client,
            model_name=model_name,
            source_url=source_url,
            source_html=source_html,
        )
    except (HTTPError, URLError, OSError) as exc:
        raise PricingSnapshotError(str(exc)) from exc
    if not payload[PRICING_MODELS_KEY]:
        raise PricingSnapshotError(
            f"No Gemini model prices could be extracted from {source_url}"
        )
    metadata = {
        "source_url": source_url,
        "content_type": content_type,
        "content_sha256": hashlib.sha256(body_bytes).hexdigest(),
        "source_etag": headers.get("ETag"),
        "source_last_modified": headers.get("Last-Modified"),
    }
    return payload, metadata


def _find_model_pricing_payload(
    pricing_snapshot: dict[str, Any],
    model_name: str,
) -> tuple[str | None, dict[str, Any] | None]:
    models = pricing_snapshot.get(PRICING_MODELS_KEY, {})
    if not isinstance(models, dict):
        return None, None

    direct_payload = models.get(model_name)
    if isinstance(direct_payload, dict):
        return model_name, direct_payload

    versioned_matches = []
    for known_model, payload in models.items():
        if not isinstance(known_model, str) or not isinstance(payload, dict):
            continue
        suffix = model_name.removeprefix(known_model)
        if suffix != model_name and VERSIONED_MODEL_SUFFIX_RE.fullmatch(suffix):
            versioned_matches.append((len(known_model), known_model, payload))
    if not versioned_matches:
        return None, None
    _, known_model, payload = max(versioned_matches, key=lambda item: item[0])
    return known_model, payload


def _missing_pricing_basis(model_name: str) -> str:
    if model_name.endswith("-latest"):
        return "latest alias; no fixed price in snapshot"
    if "preview" in model_name:
        return "preview price not found in snapshot"
    return "standard token price not found"


def describe_model_pricing(
    pricing_snapshot: dict[str, Any],
    model_name: str,
) -> ModelPricingDisplay:
    matched_model_name, payload = _find_model_pricing_payload(pricing_snapshot, model_name)
    if payload is None:
        return ModelPricingDisplay(
            model_name=model_name,
            basis=_missing_pricing_basis(model_name),
        )

    pricing = payload.get(PRICING_STANDARD_KEY)
    if not isinstance(pricing, dict):
        return ModelPricingDisplay(
            model_name=model_name,
            matched_model_name=matched_model_name,
            basis="pricing entry has no standard token prices",
        )

    try:
        input_price = (
            float(pricing[PRICING_INPUT_USD_PER_MILLION_TOKENS_KEY])
            if pricing.get(PRICING_INPUT_USD_PER_MILLION_TOKENS_KEY) is not None
            else None
        )
        output_price = (
            float(pricing[PRICING_OUTPUT_USD_PER_MILLION_TOKENS_KEY])
            if pricing.get(PRICING_OUTPUT_USD_PER_MILLION_TOKENS_KEY) is not None
            else None
        )
    except (KeyError, TypeError, ValueError):
        input_price = None
        output_price = None

    basis_parts = [
        "standard token pricing"
        if input_price is not None and output_price is not None
        else "standard token price incomplete"
    ]
    if matched_model_name and matched_model_name != model_name:
        basis_parts.append(f"version of {matched_model_name}")
    notes = payload.get(PRICING_NOTES_KEY)
    if notes:
        basis_parts.append(str(notes))

    return ModelPricingDisplay(
        model_name=model_name,
        matched_model_name=matched_model_name,
        input_usd_per_million_tokens=input_price,
        output_usd_per_million_tokens=output_price,
        basis="; ".join(basis_parts),
    )


def get_model_pricing(pricing_snapshot: dict[str, Any], model_name: str) -> ModelPricing:
    pricing = describe_model_pricing(pricing_snapshot, model_name)
    if (
        pricing.input_usd_per_million_tokens is not None
        and pricing.output_usd_per_million_tokens is not None
    ):
        return ModelPricing(
            model_name=pricing.matched_model_name or model_name,
            input_usd_per_million_tokens=pricing.input_usd_per_million_tokens,
            output_usd_per_million_tokens=pricing.output_usd_per_million_tokens,
        )
    raise ValueError(f"No pricing configured for model {model_name!r}")


def estimate_usage_cost(
    pricing_snapshot: dict[str, Any] | None,
    model_name: str,
    *,
    prompt_tokens: int,
    candidate_tokens: int,
    total_tokens: int,
) -> UsageCostEstimate | None:
    if pricing_snapshot is None:
        return None
    pricing = get_model_pricing(pricing_snapshot, model_name)
    input_cost_usd = (prompt_tokens / 1_000_000) * pricing.input_usd_per_million_tokens
    output_cost_usd = (
        candidate_tokens / 1_000_000
    ) * pricing.output_usd_per_million_tokens
    return UsageCostEstimate(
        model_name=pricing.model_name,
        prompt_tokens=prompt_tokens,
        candidate_tokens=candidate_tokens,
        total_tokens=total_tokens,
        input_cost_usd=input_cost_usd,
        output_cost_usd=output_cost_usd,
        total_cost_usd=input_cost_usd + output_cost_usd,
    )
