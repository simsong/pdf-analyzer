# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .name_clustering_models import NameCluster, NameClusteringResult, NameStringRecord

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


@dataclass(frozen=True)
class _ParsedName:
    original: str
    canonical_text: str
    tokens: tuple[str, ...]
    given_tokens: tuple[str, ...]
    surname: str
    suffix: str | None
    first_token: str | None
    first_initial: str | None
    middle_initials: tuple[str, ...]
    full_token_count: int


class _UnionFind:
    def __init__(self, values: list[int]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            parent = self.find(parent)
            self.parent[value] = parent
        return parent

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _tokenize_name(name_string: str) -> tuple[str, ...]:
    tokens = re.findall(r"[A-Za-z]+", name_string)
    return tuple(token for token in tokens if token)


def _parse_name(name_string: str) -> _ParsedName:
    tokens = _tokenize_name(name_string)
    normalized_tokens = tuple(token.casefold() for token in tokens)
    if not normalized_tokens:
        return _ParsedName(
            original=name_string,
            canonical_text=name_string.strip(),
            tokens=(),
            given_tokens=(),
            surname="",
            suffix=None,
            first_token=None,
            first_initial=None,
            middle_initials=(),
            full_token_count=0,
        )

    working_tokens = list(normalized_tokens)
    suffix: str | None = None
    if working_tokens and working_tokens[-1] in _SUFFIXES:
        suffix = working_tokens.pop()

    surname = working_tokens[-1] if working_tokens else normalized_tokens[-1]
    given_tokens = tuple(working_tokens[:-1]) if len(working_tokens) > 1 else ()
    first_token = given_tokens[0] if given_tokens else None
    first_initial = first_token[0] if first_token else None
    middle_initials = tuple(token[0] for token in given_tokens[1:] if token)
    full_token_count = sum(1 for token in given_tokens if len(token) > 1)
    canonical_text = " ".join(token.strip() for token in tokens)
    return _ParsedName(
        original=name_string,
        canonical_text=canonical_text,
        tokens=normalized_tokens,
        given_tokens=given_tokens,
        surname=surname,
        suffix=suffix,
        first_token=first_token,
        first_initial=first_initial,
        middle_initials=middle_initials,
        full_token_count=full_token_count,
    )


def _surname_similarity(left: _ParsedName, right: _ParsedName) -> float:
    if not left.surname or not right.surname:
        return 0.0
    if left.surname == right.surname:
        return 1.0
    if len(left.surname) < 4 or len(right.surname) < 4:
        return 0.0
    return SequenceMatcher(a=left.surname, b=right.surname).ratio()


def _first_name_compatible(left: _ParsedName, right: _ParsedName) -> bool:
    if left.first_initial is None or right.first_initial is None:
        return False
    if left.first_initial != right.first_initial:
        return False
    if left.first_token and right.first_token:
        if len(left.first_token) > 1 and len(right.first_token) > 1:
            return left.first_token == right.first_token
    return True


def _middle_initials_conflict(left: _ParsedName, right: _ParsedName) -> bool:
    if not left.middle_initials or not right.middle_initials:
        return False
    return left.middle_initials[0] != right.middle_initials[0]


def _time_period_overlap(left: NameStringRecord, right: NameStringRecord) -> bool:
    if not left.time_period or not right.time_period:
        return False
    left_words = {token.casefold() for token in re.findall(r"[A-Za-z0-9]+", left.time_period)}
    right_words = {token.casefold() for token in re.findall(r"[A-Za-z0-9]+", right.time_period)}
    return bool(left_words & right_words)


def _should_link(left: NameStringRecord, right: NameStringRecord) -> tuple[bool, str]:
    parsed_left = _parse_name(left.name_string)
    parsed_right = _parse_name(right.name_string)
    surname_score = _surname_similarity(parsed_left, parsed_right)
    if surname_score < 0.88:
        return False, "surname mismatch"
    if not _first_name_compatible(parsed_left, parsed_right):
        return False, "first-name mismatch"
    if _middle_initials_conflict(parsed_left, parsed_right):
        return False, "middle-initial conflict"

    score = 0.0
    if surname_score == 1.0:
        score += 3.0
    else:
        score += 2.0
    if parsed_left.first_token == parsed_right.first_token and parsed_left.first_token is not None:
        score += 3.0
    else:
        score += 2.0
    if parsed_left.middle_initials and parsed_right.middle_initials:
        score += 1.0
    if _time_period_overlap(left, right):
        score += 0.5

    if score < 5.0:
        return False, "insufficient evidence"

    if surname_score == 1.0 and parsed_left.first_token == parsed_right.first_token:
        return True, "same surname and matching first name"
    if surname_score == 1.0:
        return True, "same surname and compatible initials"
    return True, "similar surname and compatible initials"


def _representative_name(records: list[NameStringRecord]) -> NameStringRecord:
    def record_score(record: NameStringRecord) -> tuple[int, int, int, str]:
        parsed = _parse_name(record.name_string)
        information_score = (
            parsed.full_token_count * 3
            + len(parsed.middle_initials) * 2
            + (1 if parsed.suffix else 0)
            + len(parsed.tokens)
        )
        return (
            information_score,
            record.mentions,
            len(record.name_string),
            record.name_string.casefold(),
        )

    return max(records, key=record_score)


def cluster_names_locally(records: list[NameStringRecord]) -> NameClusteringResult:
    ordered_records = sorted(records, key=lambda record: record.id)
    union_find = _UnionFind([record.id for record in ordered_records])
    rationales: dict[frozenset[int], str] = {}

    for index, left_record in enumerate(ordered_records):
        for right_record in ordered_records[index + 1 :]:
            should_link, rationale = _should_link(left_record, right_record)
            if should_link:
                union_find.union(left_record.id, right_record.id)
                rationales[frozenset({left_record.id, right_record.id})] = rationale

    grouped: dict[int, list[NameStringRecord]] = {}
    for record in ordered_records:
        grouped.setdefault(union_find.find(record.id), []).append(record)

    clusters: list[NameCluster] = []
    for cluster_index, member_records in enumerate(
        sorted(grouped.values(), key=lambda members: min(record.id for record in members)),
        start=1,
    ):
        representative = _representative_name(member_records)
        member_ids = tuple(sorted(record.id for record in member_records))
        member_names = tuple(
            record.name_string for record in sorted(member_records, key=lambda record: record.id)
        )
        pair_rationales = [
            f"{left_id}-{right_id}: {reason}"
            for pair_ids, reason in sorted(
                rationales.items(),
                key=lambda item: tuple(sorted(item[0])),
            )
            for left_id, right_id in [tuple(sorted(pair_ids))]
            if pair_ids <= frozenset(member_ids)
        ]
        rationale = "; ".join(pair_rationales) if pair_rationales else "singleton"
        clusters.append(
            NameCluster(
                cluster_id=cluster_index,
                representative_name_id=representative.id,
                representative_name=representative.name_string,
                member_name_ids=member_ids,
                member_names=member_names,
                rationale=rationale,
            )
        )

    return NameClusteringResult(
        method="local",
        clusters=tuple(clusters),
        metadata={"strategy": "rule_based_fuzzy_matching"},
    )
