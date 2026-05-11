# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
import re

from .name_clustering_gemini import NameClusteringError, cluster_names_with_gemini
from .name_clustering_local import cluster_names_locally
from .name_clustering_models import NameClusteringResult, NameStringRecord

LOGGER = logging.getLogger(__name__)

SUPPORTED_NAME_CLUSTERING_METHODS = frozenset({"local", "gemini"})
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def cluster_names(
    records: list[NameStringRecord],
    *,
    method: str,
    model_name: str,
    allow_gemini: bool,
) -> NameClusteringResult:
    if method == "local":
        return cluster_names_locally(records)
    if method != "gemini":
        raise ValueError(f"Unsupported name clustering method: {method!r}")
    if not allow_gemini:
        LOGGER.info(
            "Name clustering method 'gemini' requested, but Gemini calls are disabled; falling back to local clustering."
        )
        return cluster_names_locally(records)
    try:
        return cluster_names_with_gemini(records, model_name=model_name)
    except (NameClusteringError, SystemExit) as exc:
        LOGGER.warning(
            "Gemini name clustering failed (%s); falling back to local clustering.",
            exc,
        )
        return cluster_names_locally(records)


def canonicalize_name_list(
    names: list[str],
    canonical_name_by_raw: dict[str, str],
) -> list[str]:
    seen: set[str] = set()
    canonical_names: list[str] = []
    for name in names:
        cleaned = name.strip()
        if not cleaned:
            continue
        canonical = canonical_name_by_raw.get(cleaned, cleaned)
        if canonical in seen:
            continue
        seen.add(canonical)
        canonical_names.append(canonical)
    return canonical_names


def person_sort_key(name: str) -> tuple[str, str]:
    tokens = re.findall(r"[A-Za-z]+", name)
    if not tokens:
        return ("", "")
    normalized = [token.casefold() for token in tokens]
    if normalized[-1] in _SUFFIXES and len(normalized) > 1:
        surname = normalized[-2]
        given = " ".join(normalized[:-2] + [normalized[-1]])
    else:
        surname = normalized[-1]
        given = " ".join(normalized[:-1])
    return (surname, given)
