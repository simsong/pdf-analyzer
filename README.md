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

Runtime environment:

- `GEMINI_API_KEY`
  Required for any run that makes Gemini API calls.
  Not required when using `--no-gemini` to render reports from cached SQLite state only.
  Also used by the optional live Gemini test and the name-clustering comparison harness.

Config shape:

```yaml
name: Example Archive
pdf_directory: ./pdfs
output_directory: ./example-output
question: What does this archive say about bridge safety?
name_clustering: local
ignore_dirs_containing: .pdfdata
schema_version: v1
```

Optional overrides such as `model`, `workers`, `oversize_strategy`, `name_clustering`, `ignore_dirs_containing`, `prompt_version`, `schema_version`, and `synthesis_prompt_version` are also supported. `name_clustering` defaults to `local` and also supports `gemini`.

`ignore_dirs_containing` controls recursive PDF discovery. It may be one marker filename or a list of marker filenames. Any scanned directory containing one of those files is skipped, including all of its children. The default is `.pdfdata`; each run writes that hidden JSON marker into `output_directory`, so an output directory inside the PDF archive is not rescanned as source input.

## Extraction Schema

The config file uses `schema_version` to identify the structured extraction schema for cache identity. The default is `v1`. When the Pydantic schema in `src/pdf_analyzer/models.py` changes, the analyzer also adds an automatic schema fingerprint, so old cached Gemini responses are not reused with a structurally different schema.

The current config file does not accept a custom top-level `schema:` block. To extract a domain-specific activity, put that requirement in the `question`. For example:

```yaml
name: Activity Archive
pdf_directory: ./pdfs
output_directory: ./activity-output
question: For each responsive document, identify the name, date, place, and activity. Include only activities that are supported by the PDF text.
schema_version: v1
```

With the default schema, names, dates, and places are stored in typed lists. Activities are captured in the document and evidence summaries unless the application schema and report storage are extended to make activity a first-class field.

Default document-analysis schema:

```yaml
DocumentAnalysisResult:
  responsive: boolean
  relevance_score: integer  # 0-10
  summary: string
  people:
    - string
  places:
    - string
  dates:
    - string
  evidence_items:
    - page_start: integer | null
      page_end: integer | null
      summary: string
      people:
        - string
      places:
        - string
      dates:
        - string
  reasoning_notes: string | null
```

## Outputs

Each project run writes durable artifacts into `output_directory`:

- `pdf_analyzer.sqlite3`
- `report.html`
- `report.xlsx`
- `pdfs/` containing copied responsive PDFs only
- `.pdfdata` containing output metadata such as the run timestamp
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
- The only runtime environment variable currently used by the application code is `GEMINI_API_KEY`.
- A successful rerun with no new work reuses cached per-document analyses and cached project synthesis, then re-renders the reports.
- Cache reuse is keyed by the configured versions plus automatic prompt/schema fingerprints, so structural model-output changes invalidate old cached analyses even if you forget to bump YAML version strings.
