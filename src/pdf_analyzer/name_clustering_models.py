from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NameStringRecord:
    id: int
    name_string: str
    mentions: int = 0
    time_period: str | None = None
    source_filenames: tuple[str, ...] = ()
    context_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class NameCluster:
    cluster_id: int
    representative_name_id: int
    representative_name: str
    member_name_ids: tuple[int, ...]
    member_names: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class NameClusteringResult:
    method: str
    clusters: tuple[NameCluster, ...]
    metadata: dict[str, str] = field(default_factory=dict)

    def canonical_name_by_id(self) -> dict[int, str]:
        mapping: dict[int, str] = {}
        for cluster in self.clusters:
            for name_id in cluster.member_name_ids:
                mapping[name_id] = cluster.representative_name
        return mapping

    def cluster_sets(self) -> set[frozenset[int]]:
        return {frozenset(cluster.member_name_ids) for cluster in self.clusters}

