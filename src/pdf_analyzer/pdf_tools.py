# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import cached_property
from math import sqrt
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import ContentStream

from .exceptions import PreparationError
from .models import PreparedCandidate
from .utils import apply_current_umask_file_mode, file_hashes


DEFAULT_JPEG_QUALITY = 75
PDF_SIZE_LIMIT_BYTES = 50_000_000
HIGH_RESOLUTION_DPI = 72.0

logger = logging.getLogger(__name__)


@dataclass
class ImageUsage:
    page_number: int
    object_id: str
    name: str
    width_px: int
    height_px: int
    raw_bytes: int
    display_width_pts: float
    display_height_pts: float
    dpi_x: float | None
    dpi_y: float | None

    @property
    def max_dpi(self) -> float:
        values = [value for value in (self.dpi_x, self.dpi_y) if value is not None]
        return max(values, default=0.0)


@dataclass
class ImageSummary:
    object_id: str
    raw_bytes: int
    width_px: int
    height_px: int
    usage_count: int = 0
    page_numbers: set[int] = field(default_factory=set)
    max_dpi: float = 0.0

    @property
    def is_high_resolution(self) -> bool:
        return self.max_dpi > HIGH_RESOLUTION_DPI


@dataclass
class PDFImageReport:
    image_usage_count: int
    unique_image_count: int
    total_image_bytes: int
    high_res_image_count: int
    high_res_image_bytes: int
    max_dpi: float
    top_images: list[ImageSummary]

    @property
    def has_high_resolution_images(self) -> bool:
        return self.high_res_image_count > 0


@dataclass
class CompressionCandidate:
    method: str
    path: Path
    size_bytes: int
    start_page: int | None = None
    end_page: int | None = None


class PDFInspector:
    def __init__(self, pdf_path: Path):
        self.pdf_path = pdf_path

    @cached_property
    def file_size(self) -> int:
        return self.pdf_path.stat().st_size

    @cached_property
    def reader(self) -> PdfReader | None:
        try:
            return PdfReader(str(self.pdf_path))
        except (OSError, PyPdfError) as exc:
            logger.warning("Could not inspect %s with pypdf: %s", self.pdf_path, exc)
            return None

    @cached_property
    def page_count(self) -> int | None:
        if self.reader is None:
            return None
        return len(self.reader.pages)

    @cached_property
    def image_report(self) -> PDFImageReport:
        if self.reader is None:
            return PDFImageReport(
                image_usage_count=0,
                unique_image_count=0,
                total_image_bytes=0,
                high_res_image_count=0,
                high_res_image_bytes=0,
                max_dpi=0.0,
                top_images=[],
            )

        usages: list[ImageUsage] = []
        for page_index, page in enumerate(self.reader.pages, start=1):
            usages.extend(self._page_image_usages(page, page_index))

        unique_images: dict[str, ImageSummary] = {}
        for usage in usages:
            summary = unique_images.get(usage.object_id)
            if summary is None:
                summary = ImageSummary(
                    object_id=usage.object_id,
                    raw_bytes=usage.raw_bytes,
                    width_px=usage.width_px,
                    height_px=usage.height_px,
                )
                unique_images[usage.object_id] = summary
            summary.usage_count += 1
            summary.page_numbers.add(usage.page_number)
            summary.max_dpi = max(summary.max_dpi, usage.max_dpi)

        unique_values = list(unique_images.values())
        high_res = [image for image in unique_values if image.is_high_resolution]
        sorted_images = sorted(
            unique_values,
            key=lambda image: (image.is_high_resolution, image.raw_bytes, image.max_dpi),
            reverse=True,
        )
        return PDFImageReport(
            image_usage_count=len(usages),
            unique_image_count=len(unique_values),
            total_image_bytes=sum(image.raw_bytes for image in unique_values),
            high_res_image_count=len(high_res),
            high_res_image_bytes=sum(image.raw_bytes for image in high_res),
            max_dpi=max((image.max_dpi for image in unique_values), default=0.0),
            top_images=sorted_images[:10],
        )

    def _page_image_usages(self, page, page_number: int) -> list[ImageUsage]:
        return list(
            self._iter_image_usages(
                content_source=page.get_contents(),
                resources=page.get("/Resources"),
                page_number=page_number,
                initial_ctm=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
                seen_forms=set(),
            )
        )

    def _iter_image_usages(
        self,
        *,
        content_source,
        resources,
        page_number: int,
        initial_ctm: tuple[float, float, float, float, float, float],
        seen_forms: set[str],
    ):
        if self.reader is None or resources is None or content_source is None:
            return

        try:
            content_stream = ContentStream(content_source, self.reader)
        except PyPdfError as exc:
            logger.warning(
                "Could not parse content stream for %s page %s: %s",
                self.pdf_path,
                page_number,
                exc,
            )
            return

        xobjects = self._resource_xobjects(resources)
        graphics_stack: list[tuple[float, float, float, float, float, float]] = []
        current_ctm = initial_ctm

        for operands, operator in content_stream.operations:
            if operator == b"q":
                graphics_stack.append(current_ctm)
                continue
            if operator == b"Q":
                current_ctm = graphics_stack.pop() if graphics_stack else initial_ctm
                continue
            if operator == b"cm" and len(operands) == 6:
                transform = tuple(float(value) for value in operands)
                current_ctm = self._multiply_matrices(current_ctm, transform)
                continue
            if operator != b"Do" or not operands:
                continue

            name = operands[0]
            xobject = xobjects.get(name) or xobjects.get(str(name))
            if xobject is None:
                continue

            xobject = xobject.get_object()
            subtype = xobject.get("/Subtype")
            if subtype == "/Image":
                yield self._make_image_usage(
                    page_number=page_number,
                    name=str(name),
                    xobject=xobject,
                    ctm=current_ctm,
                )
                continue
            if subtype != "/Form":
                continue

            form_id = self._object_id(xobject)
            if form_id in seen_forms:
                continue
            seen_forms.add(form_id)

            form_resources = xobject.get("/Resources") or resources
            form_matrix = xobject.get("/Matrix")
            form_ctm = current_ctm
            if form_matrix and len(form_matrix) == 6:
                form_ctm = self._multiply_matrices(
                    current_ctm, tuple(float(value) for value in form_matrix)
                )

            yield from self._iter_image_usages(
                content_source=xobject,
                resources=form_resources,
                page_number=page_number,
                initial_ctm=form_ctm,
                seen_forms=seen_forms,
            )

    def _make_image_usage(self, *, page_number: int, name: str, xobject, ctm) -> ImageUsage:
        width_px = int(xobject.get("/Width", 0) or 0)
        height_px = int(xobject.get("/Height", 0) or 0)
        raw_bytes = len(getattr(xobject, "_data", b""))
        if raw_bytes == 0:
            raw_bytes = int(xobject.get("/Length", 0) or 0)

        display_width_pts = sqrt((ctm[0] ** 2) + (ctm[1] ** 2))
        display_height_pts = sqrt((ctm[2] ** 2) + (ctm[3] ** 2))
        dpi_x = None
        dpi_y = None
        if width_px > 0 and display_width_pts > 0:
            dpi_x = width_px / (display_width_pts / 72.0)
        if height_px > 0 and display_height_pts > 0:
            dpi_y = height_px / (display_height_pts / 72.0)

        return ImageUsage(
            page_number=page_number,
            object_id=self._object_id(xobject),
            name=name,
            width_px=width_px,
            height_px=height_px,
            raw_bytes=raw_bytes,
            display_width_pts=display_width_pts,
            display_height_pts=display_height_pts,
            dpi_x=dpi_x,
            dpi_y=dpi_y,
        )

    def _resource_xobjects(self, resources) -> dict:
        xobject_ref = resources.get("/XObject") if resources else None
        if not xobject_ref:
            return {}
        if isinstance(xobject_ref, dict):
            return dict(xobject_ref.items())
        try:
            xobject_dict = xobject_ref.get_object()
        except PyPdfError:
            return {}
        return dict(xobject_dict.items())

    def _object_id(self, obj) -> str:
        reference = getattr(obj, "indirect_reference", None)
        if reference is not None and getattr(reference, "idnum", None) is not None:
            generation = getattr(reference, "generation", 0)
            return f"{reference.idnum}:{generation}"
        return f"direct:{id(obj)}"

    def _multiply_matrices(self, left, right):
        a1, b1, c1, d1, e1, f1 = left
        a2, b2, c2, d2, e2, f2 = right
        return (
            (a1 * a2) + (c1 * b2),
            (b1 * a2) + (d1 * b2),
            (a1 * c2) + (c1 * d2),
            (b1 * c2) + (d1 * d2),
            (a1 * e2) + (c1 * f2) + e1,
            (b1 * e2) + (d1 * f2) + f1,
        )


def ensure_pdf_exists(pdf_path: Path) -> Path:
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not pdf_path.is_file():
        raise ValueError(f"Path is not a file: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file, got: {pdf_path}")
    return pdf_path


def page_count(pdf_path: Path) -> int | None:
    inspector = PDFInspector(pdf_path)
    return inspector.page_count


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def print_large_pdf_image_report(pdf_path: Path, inspector: PDFInspector) -> None:
    report = inspector.image_report
    logger.info(
        "Large PDF analysis for %s: file=%s, pages=%s, unique images=%s, image bytes=%s, high-res image bytes=%s, max dpi=%.1f",
        pdf_path,
        human_size(inspector.file_size),
        inspector.page_count if inspector.page_count is not None else "?",
        report.unique_image_count,
        human_size(report.total_image_bytes),
        human_size(report.high_res_image_bytes),
        report.max_dpi,
    )


def command_exists(binary: str) -> str | None:
    return shutil.which(binary)


def run_qpdf_optimization(source_pdf: Path, output_pdf: Path, jpeg_quality: int) -> CompressionCandidate | None:
    qpdf = command_exists("qpdf")
    if qpdf is None:
        logger.warning("Skipping qpdf optimization for %s because qpdf is not installed.", source_pdf)
        return None

    output_pdf.unlink(missing_ok=True)
    command = [
        qpdf,
        "--object-streams=generate",
        "--stream-data=compress",
        "--recompress-flate",
        "--compression-level=9",
        "--remove-unreferenced-resources=yes",
        "--optimize-images",
        f"--jpeg-quality={jpeg_quality}",
        str(source_pdf),
        str(output_pdf),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        logger.warning("qpdf optimization failed for %s: %s", source_pdf, exc.stderr.strip())
        return None
    return CompressionCandidate(
        method="qpdf_standard",
        path=output_pdf,
        size_bytes=output_pdf.stat().st_size,
    )


def run_ghostscript_ebook(source_pdf: Path, output_pdf: Path) -> CompressionCandidate | None:
    ghostscript = command_exists("gs")
    if ghostscript is None:
        logger.warning(
            "Skipping Ghostscript /ebook compression for %s because gs is not installed.",
            source_pdf,
        )
        return None

    output_pdf.unlink(missing_ok=True)
    command = [
        ghostscript,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-dPDFSETTINGS=/ebook",
        f"-sOutputFile={output_pdf}",
        str(source_pdf),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Ghostscript /ebook compression failed for %s: %s",
            source_pdf,
            exc.stderr.strip(),
        )
        return None
    return CompressionCandidate(
        method="ghostscript_ebook",
        path=output_pdf,
        size_bytes=output_pdf.stat().st_size,
    )


def _stage_original_file(source_pdf: Path, staged_path: Path) -> None:
    if staged_path.exists():
        return
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_pdf, staged_path)
    apply_current_umask_file_mode(staged_path)


def write_pdf_range(
    reader: PdfReader,
    output_pdf: Path,
    *,
    start_page: int,
    end_page: int,
) -> int:
    output_pdf.unlink(missing_ok=True)
    writer = PdfWriter()
    for page_number in range(start_page - 1, end_page):
        writer.add_page(reader.pages[page_number])
    with output_pdf.open("wb") as handle:
        writer.write(handle)
    return output_pdf.stat().st_size


def split_pdf_into_chunks(
    source_pdf: Path,
    inspector: PDFInspector,
    *,
    output_dir: Path,
    chunk_prefix: str,
) -> list[CompressionCandidate]:
    if inspector.reader is None or inspector.page_count is None:
        logger.warning("Could not split %s because the PDF could not be read.", source_pdf)
        return []

    logger.info(
        "Recursively splitting %s until every chunk is under %s.",
        source_pdf.name,
        human_size(PDF_SIZE_LIMIT_BYTES),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks = _split_pdf_range(
        reader=inspector.reader,
        output_dir=output_dir,
        chunk_prefix=chunk_prefix,
        start_page=1,
        end_page=inspector.page_count,
        chunk_id="001",
        known_size=inspector.file_size,
        is_root=True,
    )
    return chunks or []


def _split_pdf_range(
    *,
    reader: PdfReader,
    output_dir: Path,
    chunk_prefix: str,
    start_page: int,
    end_page: int,
    chunk_id: str,
    known_size: int | None = None,
    is_root: bool = False,
) -> list[CompressionCandidate] | None:
    if start_page > end_page:
        return []

    output_pdf = output_dir / f"{chunk_prefix}_chunk_{chunk_id}.pdf"
    if known_size is None or not is_root:
        size_bytes = write_pdf_range(reader, output_pdf, start_page=start_page, end_page=end_page)
    else:
        size_bytes = known_size

    if size_bytes <= PDF_SIZE_LIMIT_BYTES:
        if is_root:
            return []
        return [
            CompressionCandidate(
                method="chunk",
                path=output_pdf,
                size_bytes=size_bytes,
                start_page=start_page,
                end_page=end_page,
            )
        ]

    if start_page == end_page:
        logger.warning(
            "Page %s still exceeds %s and cannot be split further.",
            start_page,
            human_size(PDF_SIZE_LIMIT_BYTES),
        )
        output_pdf.unlink(missing_ok=True)
        return None

    midpoint = (start_page + end_page) // 2
    logger.info(
        "Chunk %s (%s-%s) is %s; splitting into %s_0 and %s_1.",
        chunk_id,
        start_page,
        end_page,
        human_size(size_bytes),
        chunk_id,
        chunk_id,
    )
    left_chunks = _split_pdf_range(
        reader=reader,
        output_dir=output_dir,
        chunk_prefix=chunk_prefix,
        start_page=start_page,
        end_page=midpoint,
        chunk_id=f"{chunk_id}_0",
    )
    right_chunks = _split_pdf_range(
        reader=reader,
        output_dir=output_dir,
        chunk_prefix=chunk_prefix,
        start_page=midpoint + 1,
        end_page=end_page,
        chunk_id=f"{chunk_id}_1",
    )
    output_pdf.unlink(missing_ok=True)
    if left_chunks is None or right_chunks is None:
        return None
    return [*left_chunks, *right_chunks]


def choose_pdf_candidates(
    source_pdf: Path,
    inspector: PDFInspector,
    *,
    output_dir: Path,
    chunk_prefix: str,
    oversize_strategy: str,
    jpeg_quality: int,
) -> list[CompressionCandidate]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if inspector.file_size <= PDF_SIZE_LIMIT_BYTES:
        staged_original = output_dir / f"{chunk_prefix}.pdf"
        _stage_original_file(source_pdf, staged_original)
        return [
            CompressionCandidate(
                method="original",
                path=staged_original,
                size_bytes=inspector.file_size,
                start_page=1,
                end_page=inspector.page_count,
            )
        ]

    print_large_pdf_image_report(source_pdf, inspector)
    if oversize_strategy == "none":
        logger.info(
            "Skipping %s: %s exceeds Gemini's 50 MB PDF limit and compression is disabled.",
            source_pdf,
            human_size(inspector.file_size),
        )
        return []

    if oversize_strategy in {"chunk", "auto"}:
        chunk_candidates = split_pdf_into_chunks(
            source_pdf,
            inspector,
            output_dir=output_dir,
            chunk_prefix=chunk_prefix,
        )
        if chunk_candidates:
            return chunk_candidates

    candidates: list[CompressionCandidate] = []
    if oversize_strategy == "qpdf":
        candidate = run_qpdf_optimization(
            source_pdf,
            output_dir / f"{chunk_prefix}_optimized.pdf",
            jpeg_quality=jpeg_quality,
        )
        if candidate is not None:
            candidates.append(candidate)

    if oversize_strategy == "ebook":
        candidate = run_ghostscript_ebook(
            source_pdf,
            output_dir / f"{chunk_prefix}_compressed.pdf",
        )
        if candidate is not None:
            candidates.append(candidate)

    usable_candidates = [
        candidate for candidate in candidates if candidate.size_bytes <= PDF_SIZE_LIMIT_BYTES
    ]
    if not usable_candidates:
        logger.info(
            "Skipping %s: no compressed candidate fell under Gemini's 50 MB limit.",
            source_pdf,
        )
        return []
    best_candidate = min(usable_candidates, key=lambda candidate: candidate.size_bytes)
    best_candidate.start_page = 1
    best_candidate.end_page = inspector.page_count
    return [best_candidate]


def prepare_candidates_for_upload(
    source_pdf: Path,
    *,
    document_sha256: str,
    work_dir: Path,
    oversize_strategy: str,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> tuple[PDFInspector, list[PreparedCandidate]]:
    try:
        source_pdf = ensure_pdf_exists(source_pdf)
        inspector = PDFInspector(source_pdf)
        candidates = choose_pdf_candidates(
            source_pdf,
            inspector,
            output_dir=work_dir,
            chunk_prefix=document_sha256,
            oversize_strategy=oversize_strategy,
            jpeg_quality=jpeg_quality,
        )
        prepared: list[PreparedCandidate] = []
        for candidate in candidates:
            candidate_sha256, _, _ = file_hashes(candidate.path)
            prepared.append(
                PreparedCandidate(
                    document_sha256=document_sha256,
                    candidate_sha256=candidate_sha256,
                    path=candidate.path,
                    size_bytes=candidate.size_bytes,
                    method=candidate.method,
                    start_page=candidate.start_page,
                    end_page=candidate.end_page,
                )
            )
        return inspector, prepared
    except (OSError, ValueError, PyPdfError) as exc:
        raise PreparationError(str(exc)) from exc
