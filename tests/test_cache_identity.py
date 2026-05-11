# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

from pdf_analyzer.cache_identity import build_query_identity
from pdf_analyzer.db import Database
from pdf_analyzer.utils import utc_now_iso


def test_build_query_identity_is_stable() -> None:
    first = build_query_identity(
        prompt_version="v1",
        schema_version="v1",
        synthesis_prompt_version="v1",
    )
    second = build_query_identity(
        prompt_version="v1",
        schema_version="v1",
        synthesis_prompt_version="v1",
    )

    assert first == second
    assert first["effective_prompt_version"].startswith("v1+pf:")
    assert first["effective_schema_version"].startswith("v1+sf:")
    assert "+spf:" in first["effective_synthesis_prompt_version"]
    assert "+ssf:" in first["effective_synthesis_prompt_version"]


def test_distinct_fingerprinted_versions_create_distinct_queries(tmp_path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    db.init_schema()

    query_one = db.get_or_create_query(
        question="What happened?",
        normalized_question="what happened?",
        model_name="gemini-test",
        prompt_version="v1+pf:aaaaaaaaaaaa",
        schema_version="v1+sf:aaaaaaaaaaaa",
        synthesis_prompt_version="v1+spf:aaaaaaaaaaaa+ssf:aaaaaaaaaaaa",
        now_iso=utc_now_iso(),
    )
    query_two = db.get_or_create_query(
        question="What happened?",
        normalized_question="what happened?",
        model_name="gemini-test",
        prompt_version="v1+pf:aaaaaaaaaaaa",
        schema_version="v1+sf:bbbbbbbbbbbb",
        synthesis_prompt_version="v1+spf:aaaaaaaaaaaa+ssf:aaaaaaaaaaaa",
        now_iso=utc_now_iso(),
    )

    assert query_one != query_two
