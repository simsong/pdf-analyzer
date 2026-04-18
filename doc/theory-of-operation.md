# Theory Of Operation

## Overview

This repository analyzes MIT PDFs for connections to Louisiana using the Gemini API.

The core problem it solves is practical rather than academic: many PDFs in this corpus are scanned image documents in the 70-100 MB range, while Gemini's PDF upload limit is 50 MB per file. The implementation therefore does local PDF inspection first, then prepares oversized inputs in a Gemini-safe form, and finally submits the prepared files as one logical document for analysis.

## Purpose

The analyzer is optimized for this workflow:

1. Take one or more local PDF files.
2. Determine whether each file can be sent directly to Gemini.
3. If not, transform the submission plan without damaging the document more than necessary.
4. Preserve original page numbering so the returned citations are still useful.
5. Archive the model output and usage metadata for later review.

The current default is to split oversized PDFs by page range and submit all resulting chunks together in a single Gemini request.

## Package Layout

- `src/analyze_pdfs/analyzer2.py`
  Main CLI. Handles argument parsing, Gemini upload and cache logic, prompt construction, response parsing, and result archival.
- `src/analyze_pdfs/pdf_tools.py`
  Shared PDF utilities. Handles local inspection, image analysis, chunk generation, and optional compression experiments.
- `src/analyze_pdfs/pdf_compress.py`
  Test CLI for the reusable PDF preparation logic. Useful for checking how an oversized file will be split or compressed without spending Gemini API calls.
- `src/analyze_pdfs/report.py`
  Reads archived JSONL output and produces either an HTML report or a text cost summary.
- `src/analyze_pdfs/pricing.py`
  Built-in token pricing lookup and cost-estimation helpers.
- `src/analyze_pdfs/mit_analyzer.py`
  Older analyzer implementation retained for comparison and reference.
- `src/analyze_pdfs/list_models.py`
  Utility for listing Gemini models available to the configured API key.

## How To Run

Analyze one or more PDFs:

```bash
uv run analyze-pdfs path/to/file.pdf
uv run analyze-pdfs file1.pdf file2.pdf file3.pdf
```

The package can also be invoked directly:

```bash
uv run python -m analyze_pdfs path/to/file.pdf
```

Test the PDF preparation path only:

```bash
uv run compress-pdf path/to/file.pdf
```

Render an HTML report:

```bash
uv run analyze-pdfs-report output.jsonl --output report.html
```

Legacy script entry point:

```bash
uv run analyze-mit path/to/file.pdf
```

## Design Overview

The implementation is intentionally split into two layers:

- PDF preparation layer
  This is local-only logic in `src/analyze_pdfs/pdf_tools.py`. It decides what bytes should be sent to Gemini.
- Gemini analysis layer
  This is the network-facing logic in `src/analyze_pdfs/analyzer2.py`. It uploads the prepared files, builds the prompt, requests structured output, and archives the result.

That separation is deliberate. The PDF-preparation work is expensive to reason about and benefits from being testable without any model calls.

## End-To-End Data Flow

For each input PDF, the current flow is:

1. Validate the path.
   `ensure_pdf_exists()` resolves the path, checks that it exists, and confirms the file extension is `.pdf`.

2. Inspect the PDF locally.
   `PDFInspector` opens the file with `pypdf` and computes:
   - file size
   - page count
   - embedded image usage
   - approximate image DPI based on page transforms

3. Choose a submission strategy.
   `choose_pdf_candidates()` decides what to submit:
   - If the file is at or below 50 MB, submit the original PDF.
   - If it is larger than 50 MB, print a diagnostic image report and then apply the selected oversize strategy.
   - The default oversize strategy is recursive chunking.

4. Produce one or more submission candidates.
   A submission candidate is represented by `CompressionCandidate`, which stores:
   - the method used
   - the local file path
   - the candidate size in bytes
   - the original start and end page range, when known

5. Upload the final candidate files.
   `get_cached_file()` uploads each local file to Gemini Files API, but first checks a local shelf cache so recently uploaded files can be reused.

6. Wait for server-side file processing.
   `wait_for_file_processing()` polls Gemini until each file is ready.

7. Build one analysis prompt.
   `build_analysis_prompt()` emits:
   - the base Louisiana-analysis instructions
   - a note that multiple PDFs may be chunks of one original document
   - an explicit page-range map for each chunk
   - a requirement that returned page numbers refer to the original document

8. Submit one Gemini request.
   `analyze_pdf()` calls `generate_content()` once with:
   - one text part containing the prompt
   - one file part per uploaded PDF or chunk

9. Parse structured output.
   The response is validated against the `AnalysisResult` Pydantic schema:
   - overall score from 0 to 10
   - a list of Louisiana connections
   - original page numbers for each connection

10. Print and archive results.
    The analyzer writes:
    - a human-readable summary to stdout
    - token counts to `cost.csv`
    - the structured result to `output.jsonl`

## Data Flow Diagram

```text
CLI args
  -> analyze_pdfs.analyzer2
  -> ensure_pdf_exists()
  -> PDFInspector
       -> file size
       -> page count
       -> image/DPI diagnostics
  -> choose_pdf_candidates()
       -> original PDF
       -> or recursive chunk set
       -> or optional compression candidate
  -> get_cached_file() for each final candidate
  -> wait_for_file_processing()
  -> build_analysis_prompt()
  -> Gemini generate_content()
  -> Pydantic validation
  -> print_result()
  -> append_cost_csv()
  -> append_jsonl()
```

## Oversized PDF Handling

Gemini's working constraint for this project is 50 MB per uploaded PDF. The code therefore treats oversized inputs as a PDF-preparation problem before analysis even begins.

### Default behavior: recursive chunking

The default behavior is:

1. Start with the full document page range.
2. Measure whether that range fits under 50 MB.
3. If not, split the range in half.
4. Recurse on both halves.
5. Stop once every resulting chunk is under the limit.

This logic lives in:

- `split_pdf_into_chunks()`
- `_split_pdf_range()`

### Why recursive chunking was chosen

Two approaches were considered:

1. Estimate the number of chunks up front and split into equal page ranges.
2. Split in half recursively until each output is valid.

The implementation uses option 2.

Reasons:

- scanned PDFs are not always uniform in bytes per page
- a fixed estimate can still leave one chunk over 50 MB
- recursive halving guarantees progress
- the page-range lineage is exact
- it avoids unnecessary image rewriting

This was an important design change: the earlier "roughly 25 MB per chunk" estimate was simple, but the recursive algorithm is more reliable.

### Chunk naming

Chunk files are written next to the source PDF and named from the original stem.

Examples:

- `report.pdf` -> `report_chunk_001_0.pdf`
- `report.pdf` -> `report_chunk_001_1.pdf`

If a chunk must be split again, the lineage continues:

- `report_chunk_001_0.pdf` can split into conceptual children `001_0_0` and `001_0_1`

The filename is only part of the story. The analyzer also tracks the exact `start_page` and `end_page` for every final chunk in memory and uses that mapping in the prompt.

## Gemini Multi-Chunk Submission

One of the most important design decisions is that all final chunks are analyzed in one Gemini request, not in separate requests.

That means the analyzer does not do this:

- analyze chunk 1
- analyze chunk 2
- analyze chunk 3
- merge partial answers afterward

Instead, it does this:

- upload all final chunks
- include them all in one `generate_content()` call
- tell Gemini they are pieces of one original document

### Why one request is better here

- the model can reason across chunk boundaries
- entities mentioned in one chunk can be linked to context in another
- page-number references can still be normalized to the original document
- the application avoids building a second-stage merge or rerank pipeline

### Prompt responsibilities

When multiple chunks are present, the prompt must do extra work. It tells Gemini:

- these files belong to one original PDF
- each file corresponds to a specific original page range
- page numbers in the answer must use original document numbering

This is a prompt-level contract rather than a server-side PDF merge.

## PDF Inspection And Image Analysis

`PDFInspector` exists for two reasons:

1. It provides required metadata such as page count.
2. It helps explain why a file is large.

For scanned issues, the answer is usually "the PDF is dominated by large embedded page images."

The inspector reports:

- number of image usages
- number of unique image XObjects
- total bytes consumed by unique embedded images
- bytes attributable to images above the 72 DPI threshold
- maximum estimated DPI
- the top embedded images by size and DPI

This report is diagnostic. It is printed for oversized PDFs so the operator can understand whether the file is scan-heavy, image-heavy, or structurally bloated.

## Compression Options

The repository still contains two optional non-default compression paths:

- `qpdf`
  Structural optimization plus image optimization.
- `ebook`
  Ghostscript with `-dPDFSETTINGS=/ebook`.

These are available through `--oversize-strategy`, but they are not the default path.

## Why Compression Is Not The Default

- aggressive image downsampling can degrade scan quality
- OCR text layers can be damaged by the wrong rewrite path
- some compression methods shrink the file very little
- splitting preserves the original visual content more faithfully

The current philosophy is:

- prefer chunking first for safety
- keep compression available for controlled experiments

## Caching Design

Gemini file uploads are cached in a local shelf database:

- default cache file: `google_cache.shelf`

Each cache entry stores:

- the absolute local path
- the Gemini remote file name
- the upload timestamp

Entries are reused for about 47 hours, slightly under Gemini's nominal 48-hour file lifetime. If Gemini has already expired or lost the file, the analyzer transparently re-uploads it.

This design keeps repeated test runs fast and avoids burning extra upload calls when the same chunk files are reused.

## Archival Outputs

The analyzer appends structured history to local files so results can be revisited later:

- `output.jsonl`
  One JSON record per analyzed source PDF.
- `cost.csv`
  Token counts used to estimate model spend.

The report pipeline uses those artifacts to build an HTML summary or a consolidated cost report.

Each archived entry includes:

- original filename
- analyzed filename or chunk marker
- page count
- processing time
- parsed result data
- Gemini usage metadata
- model version
- uploaded Gemini file names
- analyzed byte count
- compression method, if any

## Main Design Decisions

### Keep The Analyzer Schema-Driven

The Gemini response is requested as JSON and validated with Pydantic. This keeps downstream archival and comparison work much cleaner than parsing free-form prose.

### Preserve Original Page Numbering

Temporary chunk-local page numbers are not useful to a human researcher. The design therefore treats original page numbering as a first-class requirement.

### Separate PDF Preparation From Gemini Logic

The local PDF work is reusable and testable on its own. That is why `src/analyze_pdfs/pdf_tools.py` and `src/analyze_pdfs/pdf_compress.py` exist.

### Prefer Safe Transformation Over Aggressive Compression

The project deals with archival scans. Preserving legibility and any OCR layer is more important than achieving the smallest possible byte count through destructive rewriting.

### Analyze All Chunks Together

The analyzer assumes that cross-chunk context matters, and it is designed to give Gemini the full document context in one call whenever the API allows it.

## Limitations

- Chunking is based on file size, not article boundaries.
- The image-analysis report is diagnostic only; it does not yet drive split boundaries.
- The analyzer relies on Gemini following the prompt instruction to use original page numbers.
- Generated chunk files are written next to the source PDF and are deleted only after a cached multi-chunk manifest has been recorded successfully.
- If a single page is still over 50 MB by itself, the recursive splitter cannot reduce it further.

## Future Directions

Likely next improvements:

- smarter split boundaries using per-page byte estimates
- automatic cleanup policies for any temporary artifacts that remain on disk
- verification that returned page numbers fall within the declared source ranges
- better quality scoring for alternative compressed outputs
- richer ranking of candidate Louisiana connections
