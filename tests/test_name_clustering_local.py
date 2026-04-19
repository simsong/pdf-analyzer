from pdf_analyzer.name_clustering_local import cluster_names_locally
from tests.name_clustering_support import load_name_records


def test_local_name_clustering_merges_obvious_variants() -> None:
    result = cluster_names_locally(load_name_records())
    canonical_by_id = result.canonical_name_by_id()

    assert canonical_by_id[8] == canonical_by_id[21]
    assert canonical_by_id[8] == canonical_by_id[22]
    assert canonical_by_id[1] == canonical_by_id[7]
    assert canonical_by_id[2] == canonical_by_id[3]
    assert canonical_by_id[13] == canonical_by_id[15]
