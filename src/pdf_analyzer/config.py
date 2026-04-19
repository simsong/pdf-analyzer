from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .constants import (
    DEFAULT_MODEL,
    DEFAULT_OVERSIZE_STRATEGY,
    DEFAULT_PROMPT_VERSION,
    DEFAULT_SCHEMA_VERSION,
    DEFAULT_SYNTHESIS_PROMPT_VERSION,
)
from .utils import ensure_directory


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Human-readable project name.")
    pdf_directory: Path = Field(description="Directory recursively scanned for PDFs.")
    question: str = Field(description="Research question to answer from the PDF archive.")
    output_directory: Path = Field(
        description="Directory for the SQLite database, report artifacts, and report PDF copies.",
    )
    model: str = DEFAULT_MODEL
    workers: int | None = None
    oversize_strategy: str = DEFAULT_OVERSIZE_STRATEGY
    prompt_version: str = DEFAULT_PROMPT_VERSION
    schema_version: str = DEFAULT_SCHEMA_VERSION
    synthesis_prompt_version: str = DEFAULT_SYNTHESIS_PROMPT_VERSION

    config_path: Path | None = None

    @model_validator(mode="after")
    def validate_oversize_strategy(self) -> "ProjectConfig":
        allowed = {"chunk", "auto", "none", "qpdf", "ebook"}
        if self.oversize_strategy not in allowed:
            raise ValueError(
                f"oversize_strategy must be one of {sorted(allowed)}, got {self.oversize_strategy!r}"
            )
        return self

    @property
    def resolved_pdf_directory(self) -> Path:
        return Path(self.pdf_directory).expanduser().resolve()

    @property
    def resolved_output_directory(self) -> Path:
        return Path(self.output_directory).expanduser().resolve()

    @classmethod
    def from_path(cls, path: Path) -> "ProjectConfig":
        resolved = path.expanduser().resolve()
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise SystemExit(f"Config file must contain a YAML mapping: {resolved}")

        payload = _resolve_relative_paths(payload, resolved.parent)
        config = cls.model_validate(payload)
        config.config_path = resolved
        ensure_directory(config.resolved_output_directory)
        if not config.resolved_pdf_directory.exists():
            raise SystemExit(f"Configured pdf_directory does not exist: {config.resolved_pdf_directory}")
        if not config.resolved_pdf_directory.is_dir():
            raise SystemExit(f"Configured pdf_directory is not a directory: {config.resolved_pdf_directory}")
        return config


def _resolve_relative_paths(payload: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    resolved = dict(payload)
    if "root_directory" in resolved and "pdf_directory" not in resolved:
        resolved["pdf_directory"] = resolved.pop("root_directory")
    for key in ("pdf_directory", "output_directory"):
        value = resolved.get(key)
        if not value:
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        resolved[key] = candidate
    return resolved
