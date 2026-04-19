from __future__ import annotations

# pylint: disable=wrong-import-position

import os
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdf_analyzer.name_clustering_gemini import NameClusteringError, cluster_names_with_gemini
from pdf_analyzer.name_clustering_local import cluster_names_locally
from pdf_analyzer.name_clustering_models import NameClusteringResult
from tests.name_clustering_support import SAMPLE_PATH, load_name_records

ARTIFACT_DIR = TESTS_DIR / "artifacts"
LOCAL_OUTPUT_PATH = ARTIFACT_DIR / "name_clustering_local.txt"
GEMINI_OUTPUT_PATH = ARTIFACT_DIR / "name_clustering_gemini.txt"
DIFF_OUTPUT_PATH = ARTIFACT_DIR / "name_clustering_diff.txt"
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"


def render_result(result: NameClusteringResult) -> str:
    lines = [f"Method: {result.method}"]
    if result.metadata:
        lines.append(f"Metadata: {result.metadata}")
    for cluster in result.clusters:
        lines.append(
            f"Cluster {cluster.cluster_id}: rep={cluster.representative_name!r} "
            f"(id={cluster.representative_name_id})"
        )
        for member_id, member_name in zip(cluster.member_name_ids, cluster.member_names, strict=True):
            lines.append(f"  - {member_id}: {member_name}")
        lines.append(f"  rationale: {cluster.rationale}")
    return "\n".join(lines) + "\n"


def render_diff(local_result: NameClusteringResult, gemini_result: NameClusteringResult) -> str:
    lines = [
        "Local-only cluster sets:",
        *[
            f"  - {sorted(cluster_set)}"
            for cluster_set in sorted(local_result.cluster_sets() - gemini_result.cluster_sets(), key=sorted)
        ],
        "Gemini-only cluster sets:",
        *[
            f"  - {sorted(cluster_set)}"
            for cluster_set in sorted(gemini_result.cluster_sets() - local_result.cluster_sets(), key=sorted)
        ],
        "Canonical-name differences by id:",
    ]
    local_canonical = local_result.canonical_name_by_id()
    gemini_canonical = gemini_result.canonical_name_by_id()
    differing_ids = sorted(
        name_id
        for name_id in local_canonical
        if local_canonical.get(name_id) != gemini_canonical.get(name_id)
    )
    if not differing_ids:
        lines.append("  - none")
    else:
        for name_id in differing_ids:
            lines.append(
                f"  - {name_id}: local={local_canonical.get(name_id)!r}; "
                f"gemini={gemini_canonical.get(name_id)!r}"
            )
    return "\n".join(lines) + "\n"


def write_output(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    records = load_name_records(SAMPLE_PATH)
    local_result = cluster_names_locally(records)
    local_text = render_result(local_result)
    print(local_text, end="")
    write_output(LOCAL_OUTPUT_PATH, local_text)
    print(f"Wrote {LOCAL_OUTPUT_PATH}")

    if not os.environ.get("GEMINI_API_KEY"):
        skip_text = "Gemini clustering skipped because GEMINI_API_KEY is not set.\n"
        print(skip_text, end="")
        write_output(GEMINI_OUTPUT_PATH, skip_text)
        write_output(DIFF_OUTPUT_PATH, skip_text)
        return 0

    try:
        gemini_result = cluster_names_with_gemini(
            records,
            model_name=DEFAULT_GEMINI_MODEL,
        )
    except NameClusteringError as exc:
        error_text = f"Gemini clustering failed: {exc}\n"
        print(error_text, end="")
        write_output(GEMINI_OUTPUT_PATH, error_text)
        write_output(DIFF_OUTPUT_PATH, error_text)
        return 1

    gemini_text = render_result(gemini_result)
    print(gemini_text, end="")
    write_output(GEMINI_OUTPUT_PATH, gemini_text)
    print(f"Wrote {GEMINI_OUTPUT_PATH}")

    diff_text = render_diff(local_result, gemini_result)
    print(diff_text, end="")
    write_output(DIFF_OUTPUT_PATH, diff_text)
    print(f"Wrote {DIFF_OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
