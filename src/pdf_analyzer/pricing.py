from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.request import Request, urlopen


PRICING_OBJECT_NAME = "gemini-prices.google.com"
DEFAULT_PRICING_SOURCE_URL = "https://ai.google.dev/gemini-api/docs/pricing"
DEFAULT_PRICING_FETCHER = "pdf_analyzer.pricing.fetch_pricing_snapshot"


@dataclass(frozen=True)
class ModelPricing:
    model_name: str
    input_usd_per_million_tokens: float
    output_usd_per_million_tokens: float


@dataclass(frozen=True)
class UsageCostEstimate:
    model_name: str
    prompt_tokens: int
    candidate_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "br", "div", "section"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "br", "div", "section"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _strip_scripts_and_styles(html: str) -> str:
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.IGNORECASE | re.DOTALL)
    return html


def _html_to_text(html: str) -> list[str]:
    parser = _TextExtractor()
    parser.feed(_strip_scripts_and_styles(html))
    raw_text = unescape(parser.text())
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
    return [line for line in lines if line]


def _extract_model_sections(html: str) -> list[tuple[str, str]]:
    return [
        (match.group(1).strip(), match.group(2))
        for match in re.finditer(
            r"<h2[^>]*>\s*(Gemini[^<]+?)\s*</h2>(.*?)(?=<h2[^>]*>|</main>|</body>)",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]


def _extract_standard_block(section_html: str) -> str:
    match = re.search(
        r"<h3[^>]*>\s*Standard\s*</h3>(.*?)(?=<h3[^>]*>|<h2[^>]*>|</main>|</body>)",
        section_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else section_html


def _extract_first_price(line: str) -> float | None:
    match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", line)
    if not match:
        return None
    return float(match.group(1))


def _find_price_line(lines: list[str], label: str) -> str:
    label_key = label.casefold()
    for line in lines:
        if label_key in line.casefold():
            return line
    return ""


def _model_aliases(section_text_lines: list[str]) -> list[str]:
    aliases: list[str] = []
    for line in section_text_lines[:8]:
        aliases.extend(re.findall(r"gemini-[a-z0-9.\-]+", line))
    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if alias in seen:
            continue
        seen.add(alias)
        deduped.append(alias)
    return deduped


def parse_pricing_page_html(html: str, *, source_url: str = DEFAULT_PRICING_SOURCE_URL) -> dict[str, Any]:
    models: dict[str, Any] = {}
    extracted_at_source = source_url
    for display_name, section_html in _extract_model_sections(html):
        section_lines = _html_to_text(section_html)
        aliases = _model_aliases(section_lines)
        standard_lines = _html_to_text(_extract_standard_block(section_html))
        input_line = _find_price_line(standard_lines, "Input price")
        output_line = _find_price_line(standard_lines, "Output price")
        input_price = _extract_first_price(input_line)
        output_price = _extract_first_price(output_line)
        if not aliases or input_price is None or output_price is None:
            continue
        model_entry = {
            "display_name": display_name,
            "aliases": aliases,
            "standard": {
                "input_usd_per_million_tokens": input_price,
                "output_usd_per_million_tokens": output_price,
            },
        }
        for alias in aliases:
            models[alias] = model_entry

    return {
        "object_name": PRICING_OBJECT_NAME,
        "source_url": extracted_at_source,
        "pricing_mode": "standard",
        "models": models,
    }


def fetch_pricing_snapshot(*, source_url: str = DEFAULT_PRICING_SOURCE_URL) -> tuple[dict[str, Any], dict[str, Any]]:
    request = Request(
        source_url,
        headers={
            "User-Agent": "pdf-analyzer/0.1 (+https://ai.google.dev/gemini-api/docs/pricing)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=30) as response:
        body_bytes = response.read()
        headers = dict(response.headers.items())
        content_type = response.headers.get_content_type()
    html = body_bytes.decode("utf-8", errors="replace")
    payload = parse_pricing_page_html(html, source_url=source_url)
    if not payload["models"]:
        raise ValueError(f"No Gemini model prices could be parsed from {source_url}")
    metadata = {
        "source_url": source_url,
        "content_type": content_type,
        "content_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest(),
        "source_etag": headers.get("ETag"),
        "source_last_modified": headers.get("Last-Modified"),
    }
    return payload, metadata


def get_model_pricing(pricing_snapshot: dict[str, Any], model_name: str) -> ModelPricing:
    models = pricing_snapshot.get("models", {})
    if model_name in models:
        pricing = models[model_name]["standard"]
        return ModelPricing(
            model_name=model_name,
            input_usd_per_million_tokens=float(pricing["input_usd_per_million_tokens"]),
            output_usd_per_million_tokens=float(pricing["output_usd_per_million_tokens"]),
        )

    for known_model, payload in models.items():
        if model_name.startswith(known_model):
            pricing = payload["standard"]
            return ModelPricing(
                model_name=known_model,
                input_usd_per_million_tokens=float(pricing["input_usd_per_million_tokens"]),
                output_usd_per_million_tokens=float(pricing["output_usd_per_million_tokens"]),
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
