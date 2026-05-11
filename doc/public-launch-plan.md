<!-- Copyright (C) 2026 Sabinok Corporation -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Public Launch Plan

This is the launch-readiness plan for announcing `sabinok-pdf` publicly. It assumes the current implementation remains a Gemini-backed PDF archive analyzer that answers one research question over a collection of PDFs, stores durable state in SQLite, and renders HTML/XLSX reports.

## Launch Goals

The public launch should make the project credible to a first-time technical user and useful to a researcher with a real PDF collection.

Minimum launch outcomes:

- clear license and dual-license contact language
- clear installation and run instructions
- one included example config that works with the current CLI
- three documented research examples using different PDF collections
- a document-source plugin architecture, even if only local files and one cloud provider are fully implemented at launch
- a short presentation deck that explains the problem, workflow, output, and roadmap
- public-facing future directions that are concrete rather than speculative

## Current Repository Gaps

The current code and documentation already cover the core analyzer, cache model, report generation, PDF/A normalization, and name clustering. The launch blockers are around packaging, public positioning, examples, and source extensibility.

Observed gaps:

- No `LICENSE` file is present.
- `pyproject.toml` does not declare project license metadata.
- The repository is now `github.com/sabinok/pdf-analyzer`, the program name is `pdf-analyzer`, and the formal product name is Sabinok PDF Analyzer.
- `README.md` references `pdf-analyzer.example.yaml`, but that example file is not currently present.
- The only source scanner is the local filesystem path in `pdf_directory`.
- The report and examples need shareable screenshots or generated artifacts suitable for an announcement.
- Public documentation should explain that uploaded PDFs and extracted facts are sent to Gemini during non-`--no-gemini` runs.
- The project requires Python `>=3.13`, while `pyright` is configured for Python `3.12`; this should be reconciled before launch.

## License Plan

Use GPL as the default public license. Prefer `GPL-3.0-or-later` unless there is a reason to require exactly GPL 3.0.

Required files and metadata:

- `LICENSE` containing the GPL text.
- A short `COMMERCIAL-LICENSE.md` or `LICENSE-OPTIONS.md` stating that less restrictive licenses are available and that interested users should contact Simson Garfinkel.
- `pyproject.toml` license metadata.
- README license section with the same dual-license statement.

Suggested README wording:

> This project is released under the GNU General Public License. Less restrictive commercial or research licenses are available. Contact Simson Garfinkel for information.

Do not imply that existing third-party dependencies are relicensed; the dual-license language should apply only to this project code.

## Public Naming And Packaging

Before announcement, choose the public name and make the names line up.

Recommended public surface:

- Project name: Sabinok PDF Analyzer
- Python package: keep `pdf_analyzer` for now unless a package rename is worth the churn
- Preferred CLI: `pdf-analyzer`

Packaging work:

- keep `pyproject.toml` package name as `pdf-analyzer`
- add license metadata
- add project URLs if there is a public repository, issue tracker, or documentation site
- add `CHANGELOG.md`
- add a release tag plan, starting at either `0.1.0` or `0.2.0`

## Example Research Tasks

The launch needs three examples with different research personalities and different PDF structures. Each example should include a small public dataset or a documented way to obtain the PDFs, a YAML config, the research question, and a sample report artifact.

Recommended examples:

| Example | PDF collection | Research question | Why it demonstrates value |
| --- | --- | --- | --- |
| Historical archive | Declassified memos, reports, and correspondence | Who appears in the collection, what events are discussed, and how does the narrative change over time? | Shows people extraction, name clustering, dates, and evidence timelines. |
| Government accountability | Meeting packets, inspection reports, contracts, or audits | What documents discuss a named program, risk, vendor, or operational failure? | Shows responsive/non-responsive filtering, evidence rows, and report review workflow. |
| Scientific or technical review | Environmental impact reports, academic reports, standards, or technical PDFs | What claims are made about a specific method, measurement, or finding, and what evidence supports each claim? | Shows question-specific extraction across dense technical PDFs. |

Example requirements:

- Use public or redistributable PDFs.
- Keep each example small enough to run in a reasonable time and cost.
- Include expected output summaries, not just commands.
- Include estimated token/cost output from a real run where possible.
- Include one example that can be rerendered with `--no-gemini` from cached SQLite state.

## Five-Slide Presentation

Create a concise deck suitable for a public announcement or short demo.

Slide outline:

1. Problem: PDF archives are searchable by text but hard to analyze as evidence collections.
2. Approach: scan PDFs, ask one research question, store structured per-document facts, synthesize project-level answers.
3. Output: HTML/XLSX report with responsive documents, evidence timeline, people, dates, places, costs, and errors.
4. Research examples: three different collections and the questions they answer.
5. Roadmap: source plugins, custom schemas, face extraction/clustering, review workflows, and repeatable publication packages.

Deck requirements:

- Include real report screenshots, not abstract diagrams only.
- Keep it to five slides.
- Make the public name and license visible.
- Include one clear "what data leaves the machine" note for Gemini-backed runs.

## Document Source Plugin Architecture

The analyzer should separate document discovery from document analysis. Source plugins should enumerate PDFs and materialize a local copy when the existing analysis pipeline needs bytes.

Use Pydantic models for configuration and source descriptors. Avoid using dictionaries as internal structures except at external API boundaries.

Proposed config shape:

```yaml
document_source:
  type: local
  path: ./pdfs
output_directory: ./example-output
question: What does this archive say about bridge safety?
```

Backward compatibility:

- Existing `pdf_directory` configs should continue to work by mapping to `document_source: {type: local, path: ...}`.
- New docs should prefer `document_source`.

Proposed source descriptor:

```python
class SourceDocument(BaseModel):
    source_type: str
    source_uri: str
    stable_id: str
    display_path: str
    filename: str
    size_bytes: int | None = None
    modified_at: str | None = None
    etag: str | None = None
    revision_id: str | None = None
```

Plugin interface:

- `iter_documents()` yields `SourceDocument` values for PDFs under the configured source.
- `materialize(document, work_dir)` returns a local `Path` for hashing, inspection, upload preparation, and report copying.
- The database should continue to key document identity by SHA-256, while also storing source metadata for traceability.
- Source metadata should not replace content hashing; remote etags and revisions are provider-specific hints, not stable cross-provider identities.

Launch implementation sequence:

1. Introduce source config models and a plugin registry.
2. Move current recursive local scanning behind a `local` source plugin.
3. Preserve the current `scan_archive()` behavior through the local plugin.
4. Add one cloud plugin with real tests at the discovery/materialization boundary.
5. Add stubs for other providers that fail with explicit setup guidance.

## Source Provider Plan

| Source | Launch stance | Authentication and implementation notes |
| --- | --- | --- |
| Local files | Fully supported | Current behavior. Recursively scan a directory, skip directories containing configured marker files, and process `*.pdf`. |
| AWS S3 | Best first cloud provider | No OAuth app registration is needed. Use normal AWS credentials from the environment/profile/role. List with `ListObjectsV2` and `Prefix`, paginate, filter `.pdf`, then download objects to a run cache before analysis. |
| Dropbox | Stub or later plugin | Dropbox API access is OAuth 2.0 based. For development, a generated access token may be usable, but production use should use a Dropbox app, scopes, short-lived access tokens, and refresh tokens. |
| Google Drive | Stub unless OAuth is in scope | Private Drive files require OAuth 2.0 scopes and an OAuth client. For a desktop CLI, use an installed-app/desktop OAuth flow and store refresh tokens securely. API keys are not enough for private files. A service account works only for files or shared drives that grant it access, or for Workspace domain delegation. |
| Microsoft OneDrive | Stub unless OAuth is in scope | Use Microsoft Graph. Register an app in Microsoft Entra, request delegated file scopes, authenticate the user with OAuth/MSAL, and use refresh tokens or MSAL token cache. Application permissions are possible for organizations but are heavier and usually require admin consent. |

For launch, the strongest low-friction path is local files plus S3. If Google Drive must be one of the launch-supported providers, treat OAuth desktop/client registration, token storage, and consent-screen documentation as launch blockers. Do not present Google Drive, Dropbox, or OneDrive as supported until the authentication flow is implemented and documented.

Source-provider references:

- [AWS S3 `ListObjectsV2`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)
- [Dropbox OAuth guide](https://developers.dropbox.com/oauth-guide)
- [Google Drive API scopes and OAuth setup](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [Google Workspace authentication overview](https://developers.google.com/workspace/guides/auth-overview)
- [Microsoft Graph authentication concepts](https://learn.microsoft.com/en-us/graph/auth/auth-concepts)
- [OneDrive sign-in and authorization](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/getting-started/authentication?view=odsp-graph-online)

## Documentation Work

Required public docs:

- README quick start with a real example config file.
- Installation prerequisites for Python, `uv`, Ghostscript, and veraPDF.
- Gemini setup and `GEMINI_API_KEY` handling.
- "Data and privacy" section explaining local state, SQLite, copied responsive PDFs, and Gemini uploads.
- Source plugin guide with local and S3 examples.
- Example gallery page linking to the three example configs and generated reports.
- Troubleshooting page for missing system dependencies, Gemini auth failures, oversized PDFs, and PDF/A conversion failures.

Keep design details in `doc/`; keep the README short and operational.

## Future Directions

Good public roadmap items:

- custom extraction schemas beyond people, places, dates, and evidence summaries
- face extraction and clustering from PDF page images and embedded photographs
- entity clustering beyond person names, including organizations, facilities, projects, and vessels
- operator review UI for correcting responsive decisions, extracted facts, and name clusters
- export packages that bundle reports, copied PDFs, config, database, and provenance metadata
- source plugins for Google Drive, Dropbox, and OneDrive
- OCR and layout-aware extraction for scanned PDFs with weak embedded text
- citation-quality evidence exports for articles, reports, and FOIA productions
- stored plugin metadata in reports so a reader can see where each PDF came from
- optional local model or alternate LLM backends for sensitive collections

Avoid promising features that require legal or platform approvals until the setup burden is documented.

## Test And Release Checklist

Before public announcement:

- Run `make check`.
- Run one no-new-work rerender using `--no-gemini`.
- Run one small fresh Gemini-backed example.
- Verify the README quick start from a clean checkout.
- Verify the included example config file path.
- Verify generated report links open copied responsive PDFs.
- Verify the license text and project metadata.
- Verify no private PDFs, credentials, generated databases, or reports are committed accidentally.
- Tag the release and publish release notes.

## Recommended Immediate Order

1. Add license files and metadata.
2. Add the real example YAML config referenced by the README.
3. Rename or add the preferred public CLI command.
4. Build the source plugin abstraction and move local scanning behind it.
5. Implement S3 as the first cloud plugin.
6. If Google Drive is launch-required, implement the OAuth desktop/client flow; otherwise add an explicit stub with setup guidance.
7. Build the three example packages.
8. Create the five-slide announcement deck from real generated outputs.
9. Run the release checklist and tag the launch version.
