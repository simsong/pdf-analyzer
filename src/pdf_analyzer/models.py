from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int | None = Field(
        default=None,
        description="1-based starting page number in the original PDF.",
    )
    summary: str = Field(
        description="A concise paraphrase of the responsive material on this page."
    )
    people: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)


class DocumentAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responsive: bool = False
    relevance_score: int = Field(
        default=1,
        description="0-10 relevance score for the configured question.",
    )
    summary: str = Field(
        description="A brief document-level answer to the configured question."
    )
    people: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    reasoning_notes: str | None = None


class ProjectSynthesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(description="Project-level answer synthesized across PDFs.")
    key_findings: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    responsive_document_count: int = 0
    total_documents_considered: int = 0
    reasoning_notes: str | None = None


@dataclass(frozen=True)
class PreparedCandidate:
    document_sha256: str
    candidate_sha256: str
    path: Path
    size_bytes: int
    method: str
    start_page: int | None = None
    end_page: int | None = None

