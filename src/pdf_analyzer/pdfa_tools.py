from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

from .utils import apply_current_umask_file_mode

LOGGER = logging.getLogger(__name__)

PDF_KEY_ROOT = "/Root"
PDF_KEY_ACRO_FORM = "/AcroForm"
PDF_KEY_ADDITIONAL_ACTIONS = "/AA"
PDF_KEY_EMBEDDED_FILE = "/EF"
PDF_KEY_FILE = "/F"
PDF_KEY_FILE_SPEC = "/Filespec"
PDF_KEY_JAVASCRIPT = "/JavaScript"
PDF_KEY_JS = "/JS"
PDF_KEY_NAMES = "/Names"
PDF_KEY_OPEN_ACTION = "/OpenAction"
PDF_KEY_ACTION_TYPE = "/S"
PDF_KEY_TYPE = "/Type"


class PDFAAnalysis(BaseModel):
    is_encrypted: bool = False
    has_javascript: bool = False
    has_interactive_forms: bool = False
    has_external_dependencies: bool = False
    error: str | None = None

    @property
    def requires_pdfa_conversion(self) -> bool:
        return any(
            (
                self.is_encrypted,
                self.has_javascript,
                self.has_interactive_forms,
                self.has_external_dependencies,
            )
        )

    @property
    def issue_names(self) -> tuple[str, ...]:
        issues: list[str] = []
        if self.is_encrypted:
            issues.append("encrypted")
        if self.has_javascript:
            issues.append("javascript")
        if self.has_interactive_forms:
            issues.append("interactive_forms")
        if self.has_external_dependencies:
            issues.append("external_dependencies")
        return tuple(issues)


class PDFAConversionError(RuntimeError):
    pass


def ghostscript_path() -> str | None:
    return shutil.which("gs")


def ensure_ghostscript_available() -> str:
    path = ghostscript_path()
    if path is None:
        raise PDFAConversionError(
            "Ghostscript (gs) is required for PDF/A conversion but was not found in PATH."
        )
    return path


def analyze_pdf(input_path: Path) -> PDFAAnalysis:
    input_path = Path(input_path)
    try:
        reader = PdfReader(str(input_path), strict=False)
    except (OSError, PyPdfError) as exc:
        LOGGER.warning("Could not analyze PDF/A conversion triggers for %s: %s", input_path, exc)
        return PDFAAnalysis(error=str(exc))

    analysis = PDFAAnalysis(is_encrypted=reader.is_encrypted)
    if reader.is_encrypted and not _decrypt_with_empty_password(reader, input_path):
        return analysis

    root = _root_object(reader)
    if root is None:
        return analysis

    return analysis.model_copy(
        update={
            "has_javascript": _has_javascript(root),
            "has_interactive_forms": PDF_KEY_ACRO_FORM in root,
            "has_external_dependencies": _has_external_dependencies(root),
        }
    )


def ensure_pdfa_if_needed(pdf_path: Path) -> PDFAAnalysis:
    pdf_path = Path(pdf_path)
    analysis = analyze_pdf(pdf_path)
    if not analysis.requires_pdfa_conversion:
        return analysis

    temp_path = pdf_path.with_name(f".{pdf_path.stem}.pdfa.tmp.pdf")
    issues = ", ".join(analysis.issue_names)
    LOGGER.info("Converting report PDF copy to PDF/A: %s (%s)", pdf_path, issues)
    if not convert_to_pdfa(pdf_path, temp_path):
        temp_path.unlink(missing_ok=True)
        raise PDFAConversionError(f"Could not convert {pdf_path} to PDF/A after detecting: {issues}")

    temp_path.replace(pdf_path)
    apply_current_umask_file_mode(pdf_path)
    return analysis


def convert_to_pdfa(input_path: Path, output_path: Path) -> bool:
    input_path = Path(input_path)
    output_path = Path(output_path)
    try:
        ghostscript = ensure_ghostscript_available()
    except PDFAConversionError:
        LOGGER.warning("Cannot convert %s to PDF/A because Ghostscript is not installed.", input_path)
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    command = [
        ghostscript,
        "-dPDFA=2",
        "-dBATCH",
        "-dNOPAUSE",
        "-dNOOUTERSAVE",
        "-dQUIET",
        "-sColorConversionStrategy=RGB",
        "-sProcessColorModel=DeviceRGB",
        "-sDEVICE=pdfwrite",
        "-dPDFACompatibilityPolicy=1",
        f"-sOutputFile={output_path}",
        str(input_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip()
        LOGGER.warning("Ghostscript PDF/A conversion failed for %s: %s", input_path, message)
        output_path.unlink(missing_ok=True)
        return False
    return output_path.exists()


def _decrypt_with_empty_password(reader: PdfReader, input_path: Path) -> bool:
    try:
        return bool(reader.decrypt(""))
    except (PyPdfError, NotImplementedError, ValueError) as exc:
        LOGGER.info("Could not inspect encrypted PDF %s after empty-password decrypt: %s", input_path, exc)
        return False


def _root_object(reader: PdfReader) -> DictionaryObject | None:
    try:
        root = _resolve_pdf_object(reader.trailer.get(PDF_KEY_ROOT))
    except PyPdfError as exc:
        LOGGER.warning("Could not resolve PDF root object: %s", exc)
        return None
    if isinstance(root, DictionaryObject):
        return root
    return None


def _has_javascript(root: DictionaryObject) -> bool:
    for obj in _walk_pdf_objects(root):
        if not isinstance(obj, DictionaryObject):
            continue
        if PDF_KEY_JS in obj or PDF_KEY_JAVASCRIPT in obj:
            return True
        if obj.get(PDF_KEY_ACTION_TYPE) == PDF_KEY_JAVASCRIPT:
            return True
        if PDF_KEY_OPEN_ACTION in obj or PDF_KEY_ADDITIONAL_ACTIONS in obj:
            action_values = [obj.get(PDF_KEY_OPEN_ACTION), obj.get(PDF_KEY_ADDITIONAL_ACTIONS)]
            if any(_action_has_javascript(action) for action in action_values):
                return True
        names = _resolve_quietly(obj.get(PDF_KEY_NAMES))
        if isinstance(names, DictionaryObject) and PDF_KEY_JAVASCRIPT in names:
            return True
    return False


def _action_has_javascript(value: Any) -> bool:
    resolved = _resolve_quietly(value)
    if isinstance(resolved, DictionaryObject):
        return resolved.get(PDF_KEY_ACTION_TYPE) == PDF_KEY_JAVASCRIPT or PDF_KEY_JS in resolved
    if isinstance(resolved, ArrayObject):
        return any(_action_has_javascript(item) for item in resolved)
    return False


def _has_external_dependencies(root: DictionaryObject) -> bool:
    for obj in _walk_pdf_objects(root):
        if isinstance(obj, StreamObject) and PDF_KEY_FILE in obj:
            return True
        if not isinstance(obj, DictionaryObject):
            continue
        if obj.get(PDF_KEY_TYPE) == PDF_KEY_FILE_SPEC and PDF_KEY_FILE in obj and PDF_KEY_EMBEDDED_FILE not in obj:
            return True
    return False


def _walk_pdf_objects(initial: Any):
    stack = [initial]
    seen_refs: set[tuple[int, int]] = set()
    seen_objects: set[int] = set()
    while stack:
        value = stack.pop()
        if isinstance(value, IndirectObject):
            reference = (value.idnum, value.generation)
            if reference in seen_refs:
                continue
            seen_refs.add(reference)
            value = _resolve_quietly(value)
            if value is None:
                continue

        object_id = id(value)
        if object_id in seen_objects:
            continue
        seen_objects.add(object_id)
        yield value

        if isinstance(value, DictionaryObject):
            stack.extend(value.values())
        elif isinstance(value, ArrayObject):
            stack.extend(value)


def _resolve_quietly(value: Any) -> Any:
    try:
        return _resolve_pdf_object(value)
    except PyPdfError:
        return None


def _resolve_pdf_object(value: Any) -> Any:
    if isinstance(value, IndirectObject):
        return value.get_object()
    return value
