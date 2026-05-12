# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import (
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_MARKER_FILENAME,
    DEFAULT_OVERSIZE_STRATEGY,
    DEFAULT_PROMPT_VERSION,
    DEFAULT_REPORT_HTML,
    DEFAULT_SCHEMA_VERSION,
    DEFAULT_SYNTHESIS_PROMPT_VERSION,
)
from .name_clustering import SUPPORTED_NAME_CLUSTERING_METHODS
from .utils import ensure_directory


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Human-readable project name.")
    pdf_directory: list[Path] = Field(description="PDF files or directories scanned for PDFs.")
    question: str = Field(description="Research question to answer from the PDF archive.")
    output_directory: Path = Field(
        description="Directory for the SQLite database, report artifacts, and report PDF clones/copies.",
    )
    model: str = DEFAULT_MODEL
    workers: int | None = None
    oversize_strategy: str = DEFAULT_OVERSIZE_STRATEGY
    name_clustering: str = "local"
    ignore_dirs_containing: list[str] = Field(
        default_factory=lambda: [DEFAULT_OUTPUT_MARKER_FILENAME],
    )
    report_html_filename: str = DEFAULT_REPORT_HTML
    normalize_pdf: bool = False
    flatten_pdf: bool = False
    flatten_dpi: int = 300
    prompt_version: str = DEFAULT_PROMPT_VERSION
    schema_version: str = DEFAULT_SCHEMA_VERSION
    synthesis_prompt_version: str = DEFAULT_SYNTHESIS_PROMPT_VERSION

    config_path: Path | None = None

    @field_validator("pdf_directory", mode="before")
    @classmethod
    def coerce_pdf_directory(cls, value: Any) -> Any:
        values = [value] if isinstance(value, str | Path) else value
        if isinstance(values, list):
            for path_value in values:
                if directory_has_outer_whitespace(path_value):
                    raise ValueError("pdf_directory paths must not have leading or trailing whitespace")
        return values

    @field_validator("output_directory", mode="before")
    @classmethod
    def reject_output_directory_whitespace(cls, value: Any) -> Any:
        if directory_has_outer_whitespace(value):
            raise ValueError("output_directory must not have leading or trailing whitespace")
        return value

    @field_validator("report_html_filename")
    @classmethod
    def validate_report_html_filename(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("report_html_filename must not have leading or trailing whitespace")
        if not value:
            raise ValueError("report_html_filename must not be empty")
        if Path(value).name != value:
            raise ValueError("report_html_filename must be a filename, not a path")
        return value

    @field_validator("ignore_dirs_containing", mode="before")
    @classmethod
    def coerce_ignore_dirs_containing(cls, value: Any) -> Any:
        if value is None:
            return [DEFAULT_OUTPUT_MARKER_FILENAME]
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="after")
    def validate_config_values(self) -> "ProjectConfig":
        allowed = {"chunk", "auto", "none", "qpdf", "ebook"}
        if self.oversize_strategy not in allowed:
            raise ValueError(
                f"oversize_strategy must be one of {sorted(allowed)}, got {self.oversize_strategy!r}"
            )
        if self.name_clustering not in SUPPORTED_NAME_CLUSTERING_METHODS:
            raise ValueError(
                "name_clustering must be one of "
                f"{sorted(SUPPORTED_NAME_CLUSTERING_METHODS)}, got {self.name_clustering!r}"
            )
        if not self.pdf_directory:
            raise ValueError("pdf_directory must include at least one input path")
        if self.flatten_dpi <= 0:
            raise ValueError("flatten_dpi must be positive")
        for marker_filename in self.ignore_dirs_containing:
            if not marker_filename.strip():
                raise ValueError("ignore_dirs_containing marker filenames must be non-empty")
            if Path(marker_filename).name != marker_filename:
                raise ValueError(
                    "ignore_dirs_containing entries must be marker filenames, not paths: "
                    f"{marker_filename!r}"
                )
        return self

    @property
    def resolved_pdf_input_paths(self) -> list[Path]:
        return [Path(path).expanduser().resolve() for path in self.pdf_directory]

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
        for input_path in config.resolved_pdf_input_paths:
            if not input_path.exists():
                raise SystemExit(f"Configured pdf_directory path does not exist: {input_path}")
            if input_path.is_file() and input_path.suffix.casefold() != ".pdf":
                raise SystemExit(f"Configured pdf_directory file is not a PDF: {input_path}")
            if not input_path.is_file() and not input_path.is_dir():
                raise SystemExit(f"Configured pdf_directory path is not a file or directory: {input_path}")
        return config


def _resolve_relative_paths(payload: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    resolved = dict(payload)
    if "root_directory" in resolved and "pdf_directory" not in resolved:
        resolved["pdf_directory"] = resolved.pop("root_directory")
    input_paths = resolved.get("pdf_directory")
    if input_paths:
        values = input_paths if isinstance(input_paths, list) else [input_paths]
        resolved["pdf_directory"] = [
            _resolve_config_path(value, base_dir, "pdf_directory") for value in values
        ]
    output_directory = resolved.get("output_directory")
    if output_directory:
        resolved["output_directory"] = _resolve_config_path(
            output_directory,
            base_dir,
            "output_directory",
        )
    return resolved


def directory_has_outer_whitespace(value: Any) -> bool:
    return isinstance(value, str | Path) and str(value) != str(value).strip()


def _resolve_config_path(value: Any, base_dir: Path, key: str) -> Path:
    if directory_has_outer_whitespace(value):
        raise SystemExit(f"Config value {key} must not have leading or trailing whitespace")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate
