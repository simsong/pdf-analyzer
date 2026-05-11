# Fact Extraction

## Purpose

`pdf_analyzer` is designed to answer one configured research question over a directory tree of PDFs.

The fact-extraction pipeline is responsible for:

- scanning and hashing PDFs
- preparing uploadable Gemini candidates
- extracting per-document facts and evidence rows
- storing those facts in SQLite
- synthesizing a project-level answer
- rendering HTML and XLSX reports from cached state

## Configuration Inputs

Each run is driven by one YAML file containing:

- `name`
- `pdf_directory`
- `output_directory`
- `question`

Important optional controls include:

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

## Runtime Environment

The application code currently uses one runtime environment variable:

- `GEMINI_API_KEY`
  Required when the run needs to call Gemini for:
  - document analysis
  - project synthesis
  - Gemini-backed name clustering
  - Gemini-backed pricing extraction

The optional live test and the standalone name-clustering comparison harness also check for `GEMINI_API_KEY` before attempting live Gemini calls.

When the analyzer is run with `--no-gemini`, it can regenerate reports from stored SQLite state without `GEMINI_API_KEY`, as long as the required cached analysis and synthesis data already exist.

## Per-Document Extraction

For each PDF that needs analysis, the system:

1. scans the local archive and computes SHA-256 for the source PDF
2. prepares one or more upload candidates
3. reuses a still-valid Gemini upload when possible
4. otherwise uploads the prepared candidate bytes
5. asks Gemini for structured JSON matching the document-analysis schema
6. stores the result in SQLite

The current document-analysis schema includes:

- `responsive`
- `relevance_score`
- document summary
- people
- places
- dates
- evidence rows

Each evidence row is expected to represent one responsive page or one contiguous responsive page range, with paraphrased summary text rather than direct quotation.

## Oversized PDFs

Large PDFs are handled before Gemini sees them.

The current system can:

- keep a PDF as-is when it is small enough
- split large PDFs into chunks
- preserve original page numbering for evidence reporting

The default behavior is chunking.

## Stored Facts

The SQLite database records:

- discovered documents and paths
- uploaded Gemini file handles
- per-document analyses
- extracted evidence rows
- project syntheses
- Gemini call logs
- token counts and costs
- run history

This allows reruns to be idempotent and cheap when no new analysis is required.

## Project-Level Synthesis

The synthesis step does not reread raw PDFs directly. Instead, it uses the stored per-document outputs.

The synthesis input is built from:

- the project question
- total document count
- successful document count
- responsive-document summaries
- responsive-document people, places, and dates
- responsive evidence rows with page references

Gemini then returns a structured project answer with:

- an answer
- key findings
- people
- places
- dates

## Reporting Outputs

The extracted facts drive both `report.html` and `report.xlsx`.

Important HTML sections and the facts they use:

- `Run Summary`
  Uses stored document counts, page counts, token totals, cost totals, and error counts.
- `Project Answer`
  Uses the stored project synthesis.
- `Responsive Index`
  Uses evidence rows sorted chronologically by the earliest detected date in each row.
- `People`
  Uses extracted person names after report-time name clustering.
- `Responsive Evidence Timeline`
  Uses the stored evidence rows, page references, canonical people, and document links.
- `Responsive Documents`
  Uses responsive document summaries plus their evidence rows.
- `Errors`
  Uses failed and unanalyzed document states from SQLite.

## What The Extractor Does Not Do

The current fact-extraction system does not try to:

- search the web for more PDFs
- spider external sites for supporting evidence
- rename source PDFs in the archive
- quote the source PDFs directly in the output

Its job is to answer the configured question from the PDFs already present in the configured archive.
