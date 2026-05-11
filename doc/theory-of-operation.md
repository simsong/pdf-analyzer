<!-- Copyright (C) 2026 Sabinok Corporation -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Theory Of Operation

## Overview

`pdf_analyzer` answers one configured research question over a recursively scanned archive of PDFs. The current workflow is YAML-driven, stores durable state in SQLite, reuses prior Gemini uploads and analyses when possible, and produces HTML plus XLSX reports for operator review.

The system is optimized for:

1. large local PDF archives
2. repeatable reruns against the same archive and question
3. traceability of model calls, failures, and costs
4. per-document evidence extraction followed by project-level synthesis

For focused details, see:

- [doc/fact-extraction.md](/Users/simsong/gits/sabinok-pdf/doc/fact-extraction.md)
- [doc/name-matching.md](/Users/simsong/gits/sabinok-pdf/doc/name-matching.md)

## Configuration

Each project is defined by one YAML file with:

- `name`
- `pdf_directory`
- `output_directory`
- `question`

Optional fields include:

- `model`
- `workers`
- `oversize_strategy`
- `name_clustering`
- `ignore_dirs_containing`
- `report_html_filename`
- `flatten_pdf`
- `flatten_dpi`
- `prompt_version`
- `schema_version`
- `synthesis_prompt_version`

One YAML file corresponds to one SQLite database and one output directory.
The configured version strings are still supported, but the analyzer also computes automatic prompt/schema fingerprints so cache invalidation follows actual structural changes.

## Runtime Environment

The current runtime environment surface is intentionally small.

Application code uses:

- `GEMINI_API_KEY`

This variable is required only for runs that make Gemini API calls. Report-only rerenders using `--no-gemini` can run without it if the needed cached state is already present in SQLite.

## Durable State

The analyzer writes the following durable outputs into `output_directory`:

- `pdf_analyzer.sqlite3`
- the configured HTML report, defaulting to `report.html`
- `report.xlsx`
- `pdfs/` containing copied responsive PDFs only; copied PDFs that are not valid PDF/A or that fail cross-domain policy checks are normalized to PDF/A before report links are written
- `.pdfdata` output marker JSON
- `.gitignore` containing `*`, so generated output directories are ignored when they live inside the repository
- a copy of the YAML config

The SQLite database stores:

- discovered documents and paths
- SHA-256 keyed upload records
- per-document analyses
- extracted evidence rows
- project synthesis
- Gemini call logs and token/cost totals
- run history

## Temporary State

Prepared upload candidates such as staged originals, chunks, and compressed variants are created in a run-scoped temporary directory via `tempfile.TemporaryDirectory()`. They are disposable scratch files and are removed automatically when the run ends.

## End-To-End Flow

1. Load the YAML config.
2. Open or initialize `pdf_analyzer.sqlite3`.
3. Recursively scan `pdf_directory` for PDFs, skipping any directory containing a configured `ignore_dirs_containing` marker file.
4. Hash each PDF with SHA-256 and record the document and path metadata.
5. Determine which PDFs still need analysis for the active query.
6. For each pending PDF:
   - inspect the PDF locally
   - prepare one or more upload candidates
   - reuse a still-valid Gemini upload when possible
   - otherwise upload the candidate bytes
   - ask Gemini for a structured per-document result
   - store summary fields, evidence rows, failures, tokens, and cost
7. Build or reuse the cached project synthesis.
8. Render the configured HTML report and `report.xlsx`.

## Idempotence Model

The analyzer is designed to make reruns cheap and predictable.

Per-document reuse:

- if a document already has a successful analysis for the same query, it is skipped by default
- `--force` re-runs document analyses even if successful results already exist

Upload reuse:

- uploads are tracked by SHA-256 of the prepared candidate bytes
- if Gemini no longer has the uploaded file, the candidate is uploaded again

Project synthesis reuse:

- if no PDFs are queued for fresh Gemini analysis
- and there is already a successful stored synthesis for the active query
- the analyzer reuses that synthesis and only re-renders the reports

`--no-gemini` prevents new Gemini calls entirely and regenerates reports from stored SQLite state.

## Query Identity

The active query is keyed by:

- question text
- normalized question text
- model name
- effective prompt version
- effective schema version
- effective synthesis prompt version

Each effective version combines the configured version string with a fingerprint of the actual prompt template or Pydantic JSON schema in use. That allows prompt/schema evolution without overwriting older runs that were produced under different assumptions, even when the YAML version strings were not manually bumped.

## Oversized PDFs

PDFs are inspected locally before upload. The analyzer chooses one or more upload candidates depending on file size and the configured `oversize_strategy`.

The default behavior is chunking. Large PDFs are split into page-range chunks until each candidate fits the Gemini file-size limit. Chunk metadata preserves original page numbering so Gemini can return useful page references.

## Gemini Inputs

### Per-document analysis

Each document analysis sends:

- the research question
- the source filename
- one or more prepared PDF candidates
- instructions requiring structured JSON output

The per-document result includes:

- `responsive`
- `relevance_score`
- document summary
- people, places, and dates
- evidence rows with page numbers and short summaries

### Project synthesis

The synthesis step does not reread the raw PDFs. It receives a structured JSON payload built from stored per-document results:

- project question
- total document count
- successful document count
- one entry per responsive document

Each responsive document entry includes:

- source filename
- document summary
- relevance score
- people, places, and dates
- evidence items with page numbers and summaries

## Reporting

The HTML report is operator-facing and emphasizes fast review:

- `Run Summary`
  Includes total documents, total scanned pages, responsive-PDF count, responsive evidence-row count, consolidated error label, and cached token/cost totals when a pricing snapshot is available.
- `Project Answer`
  Shows the stored project-level synthesis answer, key findings, and extracted people, places, and dates.
- `Responsive Index`
  Acts as a chronological table of contents over responsive evidence rows using the earliest detected date for each row.
- `People`
  Uses configurable name clustering to produce authoritative names. The default `local` strategy applies rule-based clustering; `gemini` sends the extracted name strings plus lightweight context to Gemini. The table is sorted by last name, and each disclosure row shows the exact matched extracted name form for each mention.
- `Responsive Evidence Timeline`
  Displays one card per responsive evidence row, ordered chronologically, with date pill, key-person pill, page or page-range link into the copied PDF, canonical people list, and a link to the responsive-document entry.
- `Responsive Documents`
  Lists only responsive PDFs copied into `output_directory/pdfs/`, with expandable evidence rows showing page links and authoritative names. Report PDF copies are validated with veraPDF and inspected with `pypdf`; copies that are not valid PDF/A or contain JavaScript, encryption, signatures, embedded files, launch actions, multimedia, external file streams, interactive forms, or URI actions are normalized to PDF/A with Ghostscript before the report links to them. `flatten_pdf: true` uses Ghostscript bitmap rendering at `flatten_dpi` before PDF/A normalization.
- `Errors`
  Combines failures and unanalyzed PDFs in one operator-facing section while still distinguishing the two categories.

The XLSX report contains tabular exports for:

- evidence
- documents
- failures

## Failure Handling

Failures are first-class stored state. The database records preparation failures, upload failures, analysis failures, missing files, and explicit skipped states. The report separates genuine failures from merely unanalyzed or intentionally skipped PDFs.
