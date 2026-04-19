# Name Matching

## Purpose

`pdf_analyzer` extracts person-name strings from responsive evidence and then clusters them into authoritative names for reporting.

The goal is not full historical identity resolution. The goal is lighter-weight normalization so that obvious variants such as:

- `Bernard A. Schriever`
- `Bernard Schriever`
- `C. W. Halligan`
- `Clair Halligan`
- `Clair W. Halligan`

are grouped under one authoritative report name when the evidence supports that grouping.

## Where It Is Used

Name matching affects the report-facing presentation layer:

- the `People` section
- the `Responsive Evidence Timeline`
- the `Responsive Documents` section
- the people chips shown in `Project Answer`

The report shows authoritative names by default. In the expanded mention rows, the original matched extracted form is also shown so the operator can still see what the model actually returned.

## Current Choices

The YAML config supports:

- `name_clustering: local`
- `name_clustering: gemini`

`local` is the default.

## Current Local Implementation

The current local implementation is hand-written and rule-based. It lives in:

- [src/pdf_analyzer/name_clustering_local.py](/Users/simsong/gits/sabinok-pdf/src/pdf_analyzer/name_clustering_local.py)

It currently uses:

- tokenization of personal names
- surname comparison
- first-name and initial compatibility checks
- middle-initial conflict detection
- light use of time-period overlap
- representative-name selection that prefers fuller names

This local approach is deterministic, fast, cheap, and easy to rerun.

## Gemini Option

The Gemini option lives in:

- [src/pdf_analyzer/name_clustering_gemini.py](/Users/simsong/gits/sabinok-pdf/src/pdf_analyzer/name_clustering_gemini.py)

It sends Gemini:

- each unique extracted name string
- a stable numeric id
- mention counts
- lightweight context such as source filenames and short context notes

Gemini then returns clusters and a representative name for each cluster.

If Gemini clustering is requested but Gemini use is disabled for the run, the analyzer falls back to local clustering so report generation still completes.

## Other Possible Approaches

The project is not limited to the current hand-written matcher or Gemini. Other choices exist and may be worth revisiting later.

Examples discussed for future consideration:

- `Splink`
  A supported probabilistic record-linkage system and the most likely library choice if the project moves from hand-written rules to a maintained linkage library.
- `recordlinkage`
  An older Python record-linkage library that can still be useful conceptually, but it is not the preferred future direction here.
- `probablepeople`
  Helpful as a name-parser-style building block, though not currently used.
- `humannameparser` or similar parsing libraries
  Useful for splitting name strings into components, but not by themselves a full clustering solution.
- `dedupe`
  A general entity-resolution option, though it is aimed more broadly than just person-name normalization.

These alternatives differ in:

- whether they are deterministic or model-based
- whether they need training or labeled data
- how well they handle OCR noise and abbreviations
- how much operator trust and traceability they provide

## Design Boundary

This feature is intentionally scoped as report-oriented clustering, not authoritative biographical truth.

The system currently does not try to:

- link names to external authority files
- assign globally stable human identities across projects
- merge people based on anything stronger than the extracted archive evidence and lightweight context

## Future Work

Possible future directions include:

- a `local_splink` mode alongside the current local rules
- corpus-specific tuning for OCR damage and abbreviation patterns
- storing cluster assignments in SQLite for caching and auditability
- optional operator review of cluster splits and merges
