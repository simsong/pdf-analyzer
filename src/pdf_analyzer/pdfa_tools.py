# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject

from .utils import apply_current_umask_file_mode

LOGGER = logging.getLogger(__name__)

PDF_KEY_ROOT = "/Root"
PDF_KEY_ACRO_FORM = "/AcroForm"
PDF_KEY_ASSOCIATED_FILES = "/AF"
PDF_KEY_ADDITIONAL_ACTIONS = "/AA"
PDF_KEY_ANNOTATIONS = "/Annots"
PDF_KEY_BYTE_RANGE = "/ByteRange"
PDF_KEY_CONTENTS = "/Contents"
PDF_KEY_EMBEDDED_FILE = "/EF"
PDF_KEY_EMBEDDED_FILES = "/EmbeddedFiles"
PDF_KEY_FILE = "/F"
PDF_KEY_FILE_DECODE_PARAMS = "/FDecodeParms"
PDF_KEY_FILE_FILTER = "/FFilter"
PDF_KEY_FILE_SPEC = "/Filespec"
PDF_KEY_FILTER = "/Filter"
PDF_KEY_FIRST = "/First"
PDF_KEY_FT = "/FT"
PDF_KEY_JAVASCRIPT = "/JavaScript"
PDF_KEY_JS = "/JS"
PDF_KEY_LAUNCH = "/Launch"
PDF_KEY_MEDIA_CLIP = "/MediaClip"
PDF_KEY_UNICODE_FILE = "/UF"
PDF_KEY_NAMES = "/Names"
PDF_KEY_NEXT = "/Next"
PDF_KEY_OPEN_ACTION = "/OpenAction"
PDF_KEY_PAGE_MODE = "/PageMode"
PDF_KEY_PERMS = "/Perms"
PDF_KEY_SIG = "/Sig"
PDF_KEY_SUBTYPE = "/Subtype"
PDF_KEY_ACTION_TYPE = "/S"
PDF_KEY_TYPE = "/Type"
PDF_KEY_URI = "/URI"
PDF_KEY_XFA = "/XFA"

PDF_ACTION_KEYS = frozenset(
    (
        PDF_KEY_ADDITIONAL_ACTIONS,
        PDF_KEY_JAVASCRIPT,
        PDF_KEY_JS,
        PDF_KEY_OPEN_ACTION,
    )
)
PDF_DROP_ACTION_TYPES = frozenset(
    (
        PDF_KEY_JAVASCRIPT,
        PDF_KEY_LAUNCH,
        "/ImportData",
        "/Movie",
        "/Rendition",
        "/ResetForm",
        "/RichMediaExecute",
        "/SetOCGState",
        "/Sound",
        "/SubmitForm",
        "/Trans",
        PDF_KEY_URI,
    )
)
PDF_FILE_SPEC_KEYS = frozenset(("/DOS", PDF_KEY_EMBEDDED_FILE, PDF_KEY_FILE, "/Mac", PDF_KEY_UNICODE_FILE, "/Unix"))
PDF_MULTIMEDIA_ANNOTATION_TYPES = frozenset(("/3D", "/Movie", "/RichMedia", "/Screen", "/Sound"))
PDF_MULTIMEDIA_ACTION_TYPES = frozenset(("/GoTo3DView", "/Movie", "/Rendition", "/RichMediaExecute", "/Sound"))
PDF_LINK_ANNOTATION = "/Link"


class PDFAAnalysis(BaseModel):
    is_pdfa_valid: bool = False
    pdfa_validation_error: str | None = None
    is_encrypted: bool = False
    has_javascript: bool = False
    has_digital_signatures: bool = False
    has_embedded_files: bool = False
    has_launch_actions: bool = False
    has_multimedia: bool = False
    has_uri_actions: bool = False
    has_interactive_forms: bool = False
    has_external_dependencies: bool = False
    error: str | None = None

    @property
    def requires_normalization(self) -> bool:
        return not self.is_pdfa_valid or bool(self.issue_names)

    @property
    def requires_pdfa_conversion(self) -> bool:
        return self.requires_normalization

    @property
    def issue_names(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not self.is_pdfa_valid:
            issues.append("not_pdfa")
        if self.is_encrypted:
            issues.append("encrypted")
        if self.has_javascript:
            issues.append("javascript")
        if self.has_digital_signatures:
            issues.append("digital_signatures")
        if self.has_embedded_files:
            issues.append("embedded_files")
        if self.has_launch_actions:
            issues.append("launch_actions")
        if self.has_multimedia:
            issues.append("multimedia")
        if self.has_uri_actions:
            issues.append("uri_actions")
        if self.has_interactive_forms:
            issues.append("interactive_forms")
        if self.has_external_dependencies:
            issues.append("external_dependencies")
        return tuple(issues)


class PDFAConversionError(RuntimeError):
    pass


class PDFAFixResult(BaseModel):
    path: Path
    analysis: PDFAAnalysis | None = None
    converted: bool = False
    error: str | None = None


def ghostscript_path() -> str | None:
    return shutil.which("gs")


def verapdf_path() -> str | None:
    return shutil.which("verapdf")


def ensure_ghostscript_available() -> str:
    path = ghostscript_path()
    if path is None:
        raise PDFAConversionError(
            "Ghostscript (gs) is required for PDF/A conversion but was not found in PATH."
        )
    return path


def ensure_verapdf_available() -> str:
    path = verapdf_path()
    if path is None:
        raise PDFAConversionError(
            "veraPDF (verapdf) is required for PDF/A validation but was not found in PATH."
        )
    return path


def ensure_pdf_security_tools_available(*, flatten_pdf: bool = False) -> None:
    ensure_ghostscript_available()
    ensure_verapdf_available()


def analyze_pdf(input_path: Path) -> PDFAAnalysis:
    input_path = Path(input_path)
    pdfa_valid, pdfa_error = validate_pdfa(input_path)
    try:
        reader = PdfReader(str(input_path), strict=False)
    except (OSError, PyPdfError) as exc:
        LOGGER.warning("Could not analyze PDF/A conversion triggers for %s: %s", input_path, exc)
        return PDFAAnalysis(is_pdfa_valid=pdfa_valid, pdfa_validation_error=pdfa_error, error=str(exc))

    analysis = PDFAAnalysis(
        is_pdfa_valid=pdfa_valid,
        pdfa_validation_error=pdfa_error,
        is_encrypted=reader.is_encrypted,
    )
    if reader.is_encrypted and not _decrypt_with_empty_password(reader, input_path):
        return analysis

    root = _root_object(reader)
    if root is None:
        return analysis

    return analysis.model_copy(
        update={
            "has_javascript": _has_javascript(root),
            "has_digital_signatures": _has_digital_signatures(root),
            "has_embedded_files": _has_embedded_files(root),
            "has_launch_actions": _has_launch_actions(root),
            "has_multimedia": _has_multimedia(root),
            "has_uri_actions": _has_uri_actions(root),
            "has_interactive_forms": PDF_KEY_ACRO_FORM in root,
            "has_external_dependencies": _has_external_dependencies(root),
        }
    )


def validate_pdfa(input_path: Path) -> tuple[bool, str | None]:
    try:
        verapdf = ensure_verapdf_available()
    except PDFAConversionError as exc:
        return False, str(exc)

    command = [verapdf, "--format", "json", str(input_path)]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return False, str(exc)
    if result.returncode not in (0, 1):
        message = result.stderr.strip() or result.stdout.strip()
        return False, message or f"veraPDF exited with status {result.returncode}"

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        message = result.stderr.strip() or str(exc)
        return False, message
    report = payload.get("report")
    if isinstance(report, dict):
        jobs = report.get("jobs")
        if isinstance(jobs, list):
            validation_results = []
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                result_items = job.get("validationResult")
                if isinstance(result_items, list):
                    validation_results.extend(
                        item for item in result_items if isinstance(item, dict)
                    )
            if validation_results and all(
                item.get("compliant", False) for item in validation_results
            ):
                return True, None
            return False, "veraPDF reported non-compliant PDF/A validation"

    validation_reports = payload.get("validationReports")
    if isinstance(validation_reports, list):
        if not validation_reports:
            return False, "veraPDF reported no PDF/A validation profile"
        invalid_reports = [
            report
            for report in validation_reports
            if isinstance(report, dict) and not report.get("isCompliant", False)
        ]
        if not invalid_reports and all(
            isinstance(report, dict) and report.get("isCompliant", False)
            for report in validation_reports
        ):
            return True, None
        return False, "veraPDF reported non-compliant PDF/A validation"

    jobs = payload.get("jobs")
    if isinstance(jobs, list):
        reports = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            report = job.get("validationReport")
            if isinstance(report, dict):
                reports.append(report)
        if reports and all(report.get("isCompliant", False) for report in reports):
            return True, None
        return False, "veraPDF reported non-compliant PDF/A validation"

    return False, "Could not read veraPDF validation result"


def ensure_pdfa_if_needed(pdf_path: Path, *, flatten_pdf: bool = False, flatten_dpi: int = 300) -> PDFAAnalysis:
    pdf_path = Path(pdf_path)
    analysis = analyze_pdf(pdf_path)
    if not analysis.requires_normalization:
        return analysis

    _normalize_pdf_in_place(pdf_path, analysis, flatten_pdf=flatten_pdf, flatten_dpi=flatten_dpi)
    return analysis


def fix_pdf_in_place(pdf_path: Path, *, flatten_pdf: bool = False, flatten_dpi: int = 300) -> PDFAFixResult:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return PDFAFixResult(path=pdf_path, error="file does not exist")
    if not pdf_path.is_file():
        return PDFAFixResult(path=pdf_path, error="path is not a file")
    if pdf_path.suffix.casefold() != ".pdf":
        return PDFAFixResult(path=pdf_path, error="path is not a PDF")

    analysis = analyze_pdf(pdf_path)
    if analysis.error is not None:
        return PDFAFixResult(path=pdf_path, analysis=analysis, error=analysis.error)
    if not analysis.requires_normalization:
        return PDFAFixResult(path=pdf_path, analysis=analysis)

    try:
        _normalize_pdf_in_place(pdf_path, analysis, flatten_pdf=flatten_pdf, flatten_dpi=flatten_dpi)
    except PDFAConversionError as exc:
        return PDFAFixResult(path=pdf_path, analysis=analysis, error=str(exc))
    return PDFAFixResult(path=pdf_path, analysis=analysis, converted=True)


def _normalize_pdf_in_place(pdf_path: Path, analysis: PDFAAnalysis, *, flatten_pdf: bool, flatten_dpi: int) -> None:
    temp_path = pdf_path.with_name(f".{pdf_path.stem}.pdfa.tmp.pdf")
    issues = ", ".join(analysis.issue_names)
    LOGGER.info("Normalizing PDF to PDF/A: %s (%s)", pdf_path, issues)
    normalized = flatten_to_pdfa(pdf_path, temp_path, dpi=flatten_dpi) if flatten_pdf else convert_to_pdfa(pdf_path, temp_path)
    if not normalized:
        temp_path.unlink(missing_ok=True)
        raise PDFAConversionError(f"Could not normalize {pdf_path} to PDF/A after detecting: {issues}")

    temp_path.replace(pdf_path)
    apply_current_umask_file_mode(pdf_path)


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
    sanitized_path = output_path.with_name(f".{output_path.stem}.sanitize.tmp.pdf")
    source_path = _write_sanitized_pdf(input_path, sanitized_path)
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
        str(source_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip()
        LOGGER.warning("Ghostscript PDF/A conversion failed for %s: %s", input_path, message)
        output_path.unlink(missing_ok=True)
        return False
    finally:
        if source_path == sanitized_path:
            sanitized_path.unlink(missing_ok=True)

    return _normalized_pdf_passes_policy(input_path, output_path)


def flatten_to_pdfa(input_path: Path, output_path: Path, *, dpi: int = 300) -> bool:
    input_path = Path(input_path)
    output_path = Path(output_path)
    if dpi <= 0:
        raise ValueError("flatten_dpi must be positive")
    try:
        ensure_pdf_security_tools_available(flatten_pdf=True)
    except PDFAConversionError as exc:
        LOGGER.warning("Cannot flatten %s to PDF/A: %s", input_path, exc)
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{input_path.stem}-flatten-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        image_pdf = temp_dir / "image-only.pdf"
        flatten_command = [
            ensure_ghostscript_available(),
            "-dBATCH",
            "-dNOPAUSE",
            "-dQUIET",
            f"-r{dpi}",
            "-sDEVICE=pdfimage24",
            f"-sOutputFile={image_pdf}",
            str(input_path),
        ]
        try:
            subprocess.run(flatten_command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.strip() or exc.stdout.strip()
            LOGGER.warning("PDF bitmap rendering failed for %s: %s", input_path, message)
            return False
        if not image_pdf.exists():
            LOGGER.warning("PDF bitmap rendering did not produce an image-only PDF for %s", input_path)
            return False

        if not convert_to_pdfa(image_pdf, output_path):
            return False
    return _normalized_pdf_passes_policy(input_path, output_path)


def _normalized_pdf_passes_policy(input_path: Path, output_path: Path) -> bool:
    if not output_path.exists():
        return False
    output_analysis = analyze_pdf(output_path)
    if output_analysis.error is not None or output_analysis.requires_normalization:
        message = ", ".join(output_analysis.issue_names)
        if output_analysis.error:
            message = f"{message}; {output_analysis.error}" if message else output_analysis.error
        LOGGER.warning("Normalized PDF still fails policy for %s: %s", input_path, message)
        output_path.unlink(missing_ok=True)
        return False
    return True


def _write_sanitized_pdf(input_path: Path, output_path: Path) -> Path:
    try:
        reader = PdfReader(str(input_path), strict=False)
    except (OSError, PyPdfError) as exc:
        LOGGER.warning("Could not sanitize %s before PDF/A conversion: %s", input_path, exc)
        return input_path
    if reader.is_encrypted and not _decrypt_with_empty_password(reader, input_path):
        return input_path

    output_path.unlink(missing_ok=True)
    writer = PdfWriter()
    try:
        for page in reader.pages:
            _sanitize_pdf_object(page)
            writer.add_page(page)
    except PyPdfError as exc:
        LOGGER.warning("Could not sanitize pages in %s before PDF/A conversion: %s", input_path, exc)
        return input_path
    _sanitize_pdf_object(writer.root_object)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        writer.write(handle)
    return output_path


def _sanitize_pdf_object(value: Any, seen_refs: set[tuple[int, int]] | None = None, seen_objects: set[int] | None = None) -> None:
    if seen_refs is None:
        seen_refs = set()
    if seen_objects is None:
        seen_objects = set()

    if isinstance(value, IndirectObject):
        reference = (value.idnum, value.generation)
        if reference in seen_refs:
            return
        seen_refs.add(reference)
        value = _resolve_quietly(value)
        if value is None:
            return

    object_id = id(value)
    if object_id in seen_objects:
        return
    seen_objects.add(object_id)

    if isinstance(value, DictionaryObject):
        _sanitize_pdf_dictionary(value, seen_refs, seen_objects)
        return
    if isinstance(value, ArrayObject):
        for item in value:
            _sanitize_pdf_object(item, seen_refs, seen_objects)


def _sanitize_pdf_dictionary(value: DictionaryObject, seen_refs: set[tuple[int, int]], seen_objects: set[int]) -> None:
    for key in tuple(value.keys()):
        if key in PDF_ACTION_KEYS or key == PDF_KEY_ACRO_FORM:
            del value[key]
            continue
        if key == "/A" and _action_requires_drop(value[key]):
            del value[key]
            continue
        if key in (PDF_KEY_ASSOCIATED_FILES, PDF_KEY_PERMS):
            del value[key]
            continue
        if key == PDF_KEY_NAMES:
            names = _resolve_quietly(value[key])
            if isinstance(names, DictionaryObject):
                names.pop(PDF_KEY_JAVASCRIPT, None)
                names.pop(PDF_KEY_EMBEDDED_FILES, None)
                if not names:
                    del value[key]
        elif isinstance(value, StreamObject) and key in (PDF_KEY_FILE, PDF_KEY_FILE_FILTER, PDF_KEY_FILE_DECODE_PARAMS):
            del value[key]
        elif value.get(PDF_KEY_TYPE) == PDF_KEY_FILE_SPEC and key in PDF_FILE_SPEC_KEYS:
            del value[key]

    for item in value.values():
        _sanitize_pdf_object(item, seen_refs, seen_objects)


def _action_requires_drop(value: Any) -> bool:
    action = _resolve_quietly(value)
    if isinstance(action, DictionaryObject):
        action_type = action.get(PDF_KEY_ACTION_TYPE)
        if action_type in PDF_DROP_ACTION_TYPES or PDF_KEY_JS in action or PDF_KEY_JAVASCRIPT in action:
            return True
        next_action = action.get(PDF_KEY_NEXT)
        if next_action is not None:
            return _action_requires_drop(next_action)
    if isinstance(action, ArrayObject):
        return any(_action_requires_drop(item) for item in action)
    return False


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


def _has_digital_signatures(root: DictionaryObject) -> bool:
    for obj in _walk_pdf_objects(root):
        if not isinstance(obj, DictionaryObject):
            continue
        if obj.get(PDF_KEY_FT) == PDF_KEY_SIG or obj.get(PDF_KEY_TYPE) == PDF_KEY_SIG:
            return True
        if PDF_KEY_BYTE_RANGE in obj and PDF_KEY_CONTENTS in obj:
            return True
        perms = _resolve_quietly(obj.get(PDF_KEY_PERMS))
        if isinstance(perms, DictionaryObject) and perms:
            return True
    return False


def _has_embedded_files(root: DictionaryObject) -> bool:
    for obj in _walk_pdf_objects(root):
        if not isinstance(obj, DictionaryObject):
            continue
        if PDF_KEY_ASSOCIATED_FILES in obj:
            return True
        if obj.get(PDF_KEY_SUBTYPE) == "/FileAttachment":
            return True
        if obj.get(PDF_KEY_TYPE) == PDF_KEY_FILE_SPEC and PDF_KEY_EMBEDDED_FILE in obj:
            return True
        names = _resolve_quietly(obj.get(PDF_KEY_NAMES))
        if isinstance(names, DictionaryObject) and PDF_KEY_EMBEDDED_FILES in names:
            return True
    return False


def _has_launch_actions(root: DictionaryObject) -> bool:
    return _has_action_type(root, {PDF_KEY_LAUNCH})


def _has_uri_actions(root: DictionaryObject) -> bool:
    return _has_action_type(root, {PDF_KEY_URI})


def _has_multimedia(root: DictionaryObject) -> bool:
    for obj in _walk_pdf_objects(root):
        if not isinstance(obj, DictionaryObject):
            continue
        if obj.get(PDF_KEY_SUBTYPE) in PDF_MULTIMEDIA_ANNOTATION_TYPES:
            return True
        if obj.get(PDF_KEY_ACTION_TYPE) in PDF_MULTIMEDIA_ACTION_TYPES:
            return True
        if PDF_KEY_MEDIA_CLIP in obj or "/RichMediaContent" in obj or "/3DD" in obj:
            return True
    return False


def _has_action_type(root: DictionaryObject, action_types: set[str] | frozenset[str]) -> bool:
    for obj in _walk_pdf_objects(root):
        if isinstance(obj, DictionaryObject) and obj.get(PDF_KEY_ACTION_TYPE) in action_types:
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
        if isinstance(obj, StreamObject) and any(
            key in obj for key in (PDF_KEY_FILE, PDF_KEY_FILE_FILTER, PDF_KEY_FILE_DECODE_PARAMS)
        ):
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check PDFs for active/external features and convert flagged files to PDF/A in place.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF files to check and fix in place.")
    parser.add_argument("--flatten-pdf", action="store_true", help="Normalize by rendering pages to bitmap PDF first.")
    parser.add_argument("--flatten-dpi", type=int, default=300, help="DPI used with --flatten-pdf.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.CRITICAL,
        format="%(levelname)s: %(message)s",
    )

    failed = False
    for pdf_path in args.pdfs:
        result = fix_pdf_in_place(
            pdf_path,
            flatten_pdf=args.flatten_pdf,
            flatten_dpi=args.flatten_dpi,
        )
        if result.error is not None:
            failed = True
            if result.error in {"file does not exist", "path is not a file", "path is not a PDF"}:
                print(result.path)
            continue
        if result.converted:
            print(result.path)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
