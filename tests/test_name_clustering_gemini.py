# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Any

from pdf_analyzer.name_clustering_gemini import cluster_names_with_gemini
from tests.name_clustering_support import load_name_records


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate_content(self, **_: Any) -> _FakeResponse:
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.models = _FakeModels(text)


def test_gemini_name_clustering_parses_structured_response() -> None:
    records = load_name_records()
    response_text = """
    {
      "clusters": [
        {
          "representative_name_id": 1,
          "representative_name": "Bernard A. Schriever",
          "member_name_ids": [1, 7],
          "rationale": "Same surname and first name."
        },
        {
          "representative_name_id": 8,
          "representative_name": "Clair W. Halligan",
          "member_name_ids": [8, 21, 22],
          "rationale": "Initials and fuller name match."
        },
        {
          "representative_name_id": 20,
          "representative_name": "Albert G. Hill",
          "member_name_ids": [5, 19, 20],
          "rationale": "Initial-based and nickname variants align."
        },
        {
          "representative_name_id": 2,
          "representative_name": "Gordon N. Thayer",
          "member_name_ids": [2, 3],
          "rationale": "Middle initial omitted in one variant."
        },
        {
          "representative_name_id": 15,
          "representative_name": "John F. Jacobs",
          "member_name_ids": [13, 15],
          "rationale": "Initials expand cleanly."
        },
        {
          "representative_name_id": 4,
          "representative_name": "John L. Lombardo",
          "member_name_ids": [4],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 6,
          "representative_name": "Allen Puckett",
          "member_name_ids": [6],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 9,
          "representative_name": "Carl Overhage",
          "member_name_ids": [9],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 10,
          "representative_name": "Emanuel Piore",
          "member_name_ids": [10],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 11,
          "representative_name": "H. Guyford Stever",
          "member_name_ids": [11],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 12,
          "representative_name": "Ivan Getting",
          "member_name_ids": [12],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 14,
          "representative_name": "Jerome B. Wiesner",
          "member_name_ids": [14],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 16,
          "representative_name": "R. F. Mettler",
          "member_name_ids": [16],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 17,
          "representative_name": "W. H. Radford",
          "member_name_ids": [17],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 18,
          "representative_name": "W. O. Baker",
          "member_name_ids": [18],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 23,
          "representative_name": "Eugene G. Fubini",
          "member_name_ids": [23],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 24,
          "representative_name": "Jack Ruina",
          "member_name_ids": [24],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 25,
          "representative_name": "John F. Loosbrock",
          "member_name_ids": [25],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 26,
          "representative_name": "Julius A. Stratton",
          "member_name_ids": [26],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 27,
          "representative_name": "Kenneth P. Bergquist",
          "member_name_ids": [27],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 28,
          "representative_name": "Lester R. Allen, Jr.",
          "member_name_ids": [28],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 29,
          "representative_name": "Norman Waks",
          "member_name_ids": [29],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 30,
          "representative_name": "Thomas Power",
          "member_name_ids": [30],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 31,
          "representative_name": "Walter S. Attridge",
          "member_name_ids": [31],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 32,
          "representative_name": "William E. Holden",
          "member_name_ids": [32],
          "rationale": "Singleton."
        },
        {
          "representative_name_id": 33,
          "representative_name": "William Sen",
          "member_name_ids": [33],
          "rationale": "Singleton."
        }
      ]
    }
    """
    result = cluster_names_with_gemini(
        records,
        model_name="gemini-3-flash-preview",
        client=_FakeClient(response_text),
    )

    canonical_by_id = result.canonical_name_by_id()
    assert canonical_by_id[1] == "Bernard A. Schriever"
    assert canonical_by_id[7] == "Bernard A. Schriever"
    assert canonical_by_id[8] == "Clair W. Halligan"
    assert canonical_by_id[21] == "Clair W. Halligan"
    assert canonical_by_id[22] == "Clair W. Halligan"
