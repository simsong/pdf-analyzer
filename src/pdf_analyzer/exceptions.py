class AnalyzerError(Exception):
    """Base class for analyzer-specific errors."""


class PricingSnapshotError(AnalyzerError):
    """Raised when the Gemini pricing snapshot cannot be fetched or parsed."""


class PreparationError(AnalyzerError):
    """Raised when a PDF cannot be prepared for Gemini upload."""


class UploadCandidateError(AnalyzerError):
    """Raised when a prepared PDF candidate cannot be uploaded or reused."""


class DocumentAnalysisError(AnalyzerError):
    """Raised when Gemini document analysis fails or returns invalid output."""


class ProjectSynthesisError(AnalyzerError):
    """Raised when Gemini project synthesis fails or returns invalid output."""
