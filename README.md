# MIT PDF Analyzer

This repository analyzes MIT PDFs for Louisiana connections using Gemini, with special handling for oversized scanned documents that exceed Gemini's per-file PDF limit. The code is now packaged under `src/analyze_pdfs/`, so the command-line tools work as installable package entry points instead of depending on flat top-level modules.

## Quick Start

Install dependencies:

```bash
uv sync
```

## New Archive Workflow

The repo now also contains a new package, `src/pdf_analyzer/`, for question-answering over a whole archive of PDFs. It combines:

- recursive archive scanning
- SQLite-backed idempotent reruns
- Gemini upload caching keyed by SHA-256
- oversized-PDF chunking
- per-document structured evidence extraction
- project-level synthesis
- HTML and XLSX reporting

Run it with a YAML config:

```bash
uv run pdf-analyzer pdf-analyzer.example.yaml
```

The config shape is:

```yaml
name: Example Archive
pdf_directory: ./pdfs
output_directory: ./example-output
question: What does this archive say about bridge safety?
```

Optional overrides such as `model`, `workers`, and `oversize_strategy` are also supported.

Analyze one or more PDFs:

```bash
uv run analyze-pdfs path/to/file.pdf
uv run analyze-pdfs file1.pdf file2.pdf
```

You can also invoke the package directly:

```bash
uv run python -m analyze_pdfs path/to/file.pdf
```

Inspect the oversized-PDF preparation path without making model calls:

```bash
uv run compress-pdf path/to/file.pdf
```

Render an HTML report from archived JSONL output:

```bash
uv run analyze-pdfs-report output.jsonl --output report.html
```

## Repository Layout

- `src/analyze_pdfs/`
  The Python package containing the analyzer, PDF utilities, reporting code, pricing helpers, legacy analyzer, and packaged HTML template.
- `doc/theory-of-operation.md`
  Detailed design, data flow, chunking strategy, and archival behavior.
- `README.md`
  High-level project overview and getting-started guidance.

## What The Tool Does

The main analyzer workflow is:

1. Inspect each input PDF locally.
2. Send files under 50 MB directly to Gemini.
3. Split oversized PDFs into page-range chunks when needed.
4. Submit all final chunks together as one logical document.
5. Archive structured results plus usage metadata for later reporting.

Outputs are written to `output.jsonl` and `cost.csv` by default.

## Additional Commands

- `uv run analyze-mit path/to/file.pdf`
  Runs the older legacy analyzer kept for comparison/reference.
- `uv run list-gemini-models`
  Lists Gemini models visible to the configured API key.

## More Detail

The detailed design rationale, chunking approach, prompt strategy, and report pipeline now live in [doc/theory-of-operation.md](/Users/simsong/gits/sabinok-pdf/doc/theory-of-operation.md).
