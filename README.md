# PDF Analyzer

This repository contains a Gemini-backed archive analysis tool for answering one research question over a directory tree of PDFs. The supported system is the `pdf_analyzer` package in `src/pdf_analyzer/`.

It is designed for repeatable journalism and archival work:

- recursively scan a PDF archive
- avoid re-uploading unchanged PDFs by SHA-256
- store per-document analyses and project synthesis in SQLite
- rerun idempotently
- track failures and Gemini costs
- generate HTML and XLSX reports

## Quick Start

Install dependencies:

```bash
uv sync
```

Run the analyzer with a YAML config:

```bash
uv run analyze pdf-analyzer.example.yaml
```

Config shape:

```yaml
name: Example Archive
pdf_directory: ./pdfs
output_directory: ./example-output
question: What does this archive say about bridge safety?
name_clustering: local
```

Optional overrides such as `model`, `workers`, `oversize_strategy`, and `name_clustering` are also supported. `name_clustering` defaults to `local` and also supports `gemini`.

## Outputs

Each project run writes durable artifacts into `output_directory`:

- `pdf_analyzer.sqlite3`
- `report.html`
- `report.xlsx`
- `pdfs/` containing copied responsive PDFs only
- a copy of the YAML config used for the run

Intermediate prepared PDFs are written to a temporary directory for the duration of the run and are not kept afterward.

## Report Sections

`report.html` includes these sections:

- `Run Summary`: document count, scanned pages, responsive-PDF count, evidence-row count, error summary, and cached token/cost totals when pricing is available.
- `Project Answer`: project-level synthesis answer plus key findings and extracted people, places, and dates.
- `Responsive Index`: a concise chronological table of contents linking into the detailed evidence cards.
- `People`: authoritative clustered names, sorted by last name, with expandable mention rows that show the matched extracted name variant for each evidence row.
- `Responsive Evidence Timeline`: chronological evidence cards with date pill, key person, page or page-range links, canonical people, and links back to responsive documents.
- `Responsive Documents`: one row per responsive PDF, with expandable evidence rows showing pages and authoritative names.
- `Errors`: failures plus unanalyzed documents when any exist.

## Repository Layout

- `src/pdf_analyzer/`
  Supported archive-analysis package.
- `doc/theory-of-operation.md`
  Overall architecture, data flow, and report pipeline for `pdf_analyzer`.
- `doc/fact-extraction.md`
  Focused note on document analysis, synthesis, and report data flow.
- `doc/name-matching.md`
  Focused note on authoritative-name clustering, current options, and alternative approaches.

## Notes

- `uv run analyze ...` is the preferred CLI.
- `uv run pdf-analyzer ...` remains as a compatibility alias.
- A successful rerun with no new work reuses cached per-document analyses and cached project synthesis, then re-renders the reports.
- Cache reuse is keyed by the configured versions plus automatic prompt/schema fingerprints, so structural model-output changes invalidate old cached analyses even if you forget to bump YAML version strings.
