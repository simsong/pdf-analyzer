import argparse
import logging
from pathlib import Path

from .pdf_tools import (
    DEFAULT_JPEG_QUALITY,
    PDFInspector,
    choose_pdf_candidates,
    ensure_pdf_exists,
    human_size,
)


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect oversized PDFs and run the reusable compression routine."
    )
    parser.add_argument("pdfs", nargs="+", help="Path(s) to PDF files to inspect/compress")
    parser.add_argument(
        "--oversize-strategy",
        choices=["chunk", "auto", "none", "qpdf", "ebook"],
        default="chunk",
        help="How to prepare oversized PDFs before testing them against the 50 MB limit.",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=DEFAULT_JPEG_QUALITY,
        help="JPEG quality used by qpdf image optimization.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for pdf in args.pdfs:
        source_pdf = ensure_pdf_exists(Path(pdf))
        inspector = PDFInspector(source_pdf)
        page_text = inspector.page_count if inspector.page_count is not None else "?"
        print(f"PDF: {source_pdf}")
        print(f"Original: {human_size(inspector.file_size)} | pages={page_text}")
        candidates = choose_pdf_candidates(
            source_pdf,
            inspector,
            oversize_strategy=args.oversize_strategy,
            jpeg_quality=args.jpeg_quality,
        )
        if not candidates:
            print("Result: no candidate under 50 MB")
            print()
            continue

        if len(candidates) == 1:
            candidate = candidates[0]
            print(
                f"Result: {candidate.method} -> {candidate.path} "
                f"({human_size(candidate.size_bytes)})"
            )
        else:
            print(f"Result: {len(candidates)} chunk(s)")
            for candidate in candidates:
                print(
                    f"- pages {candidate.start_page}-{candidate.end_page}: "
                    f"{candidate.path} ({human_size(candidate.size_bytes)})"
                )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
