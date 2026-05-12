# Copyright (C) 2026 Sabinok Corporation
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from pdf_analyzer.exceptions import PricingSnapshotError
from pdf_analyzer.main import discover_pdf_paths, main, write_output_marker


def test_discover_pdf_paths_skips_directories_with_ignore_markers(tmp_path: Path) -> None:
    kept_pdf = tmp_path / "keep.pdf"
    kept_pdf.write_bytes(b"%PDF-1.4\n")

    ignored_dir = tmp_path / "output"
    ignored_dir.mkdir()
    (ignored_dir / ".pdfdata").write_text("{}\n", encoding="utf-8")
    (ignored_dir / "report.pdf").write_bytes(b"%PDF-1.4\n")

    nested_ignored_dir = ignored_dir / "pdfs"
    nested_ignored_dir.mkdir()
    (nested_ignored_dir / "copy.pdf").write_bytes(b"%PDF-1.4\n")

    discovered = discover_pdf_paths([tmp_path], [".pdfdata"])

    assert [item.path for item in discovered] == [kept_pdf.resolve()]
    assert discovered[0].relative_path == Path("keep.pdf")


def test_discover_pdf_paths_accepts_multiple_files_and_directories(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first_pdf = first_dir / "first.pdf"
    first_pdf.write_bytes(b"%PDF-1.4\n")

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second_pdf = second_dir / "second.pdf"
    second_pdf.write_bytes(b"%PDF-1.4\n")

    direct_pdf = tmp_path / "direct.pdf"
    direct_pdf.write_bytes(b"%PDF-1.4\n")
    non_pdf = tmp_path / "notes.txt"
    non_pdf.write_text("not a PDF\n", encoding="utf-8")

    discovered = discover_pdf_paths([first_dir, second_dir, direct_pdf, non_pdf], [])

    assert [item.path for item in discovered] == [
        direct_pdf.resolve(),
        first_pdf.resolve(),
        second_pdf.resolve(),
    ]


def test_write_output_marker_records_timestamp(tmp_path: Path) -> None:
    write_output_marker(tmp_path)

    payload = json.loads((tmp_path / ".pdfdata").read_text(encoding="utf-8"))

    assert set(payload) == {"timestamp"}
    datetime.fromisoformat(payload["timestamp"])
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_list_models_prints_available_models_and_prices(monkeypatch, capsys) -> None:
    fake_models = [
        SimpleNamespace(
            name="models/gemini-2.5-flash",
            input_token_limit=1_048_576,
            output_token_limit=65_536,
            supported_actions=["generateContent"],
        ),
        SimpleNamespace(
            name="models/gemini-pro-latest",
            input_token_limit=2_000,
            output_token_limit=1_000,
            supported_actions=["generateContent"],
        ),
        SimpleNamespace(
            name="models/gemini-embedding-001",
            input_token_limit=2_048,
            output_token_limit=1,
            supported_actions=["embedContent"],
        ),
        SimpleNamespace(
            name="models/gemini-2.5-flash-preview-tts",
            input_token_limit=8_192,
            output_token_limit=16_384,
            supported_actions=["generateContent"],
        ),
        SimpleNamespace(
            name="models/imagen-4.0-generate-001",
            input_token_limit=480,
            output_token_limit=8_192,
            supported_actions=[],
        ),
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.models = SimpleNamespace(list=lambda: fake_models)

    def fake_build_client():
        return FakeClient()

    monkeypatch.setattr("pdf_analyzer.main.build_client", fake_build_client)
    monkeypatch.setattr(
        "pdf_analyzer.main.fetch_pricing_snapshot",
        lambda *, client, model_name: (
            {
                "models": {
                    "gemini-2.5-flash": {
                        "display_name": "Gemini 2.5 Flash",
                        "aliases": ["gemini-2.5-flash"],
                        "standard": {
                            "input_usd_per_million_tokens": 0.30,
                            "output_usd_per_million_tokens": 2.50,
                        },
                        "notes": "Lower-band standard token pricing.",
                    }
                }
            },
            {},
        ),
    )
    monkeypatch.setattr(sys, "argv", ["pdf-analyzer", "--list-models"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "MODEL ID" in output
    assert "PRICE BASIS" in output
    assert "gemini-2.5-flash" in output
    assert "$0.30" in output
    assert "$2.50" in output
    assert "Lower-band standard token pricing." in output
    assert "gemini-pro-latest" in output
    assert "latest alias; no fixed price in snapshot" in output
    assert "gemini-embedding-001" not in output
    assert "gemini-2.5-flash-preview-tts" not in output
    assert "imagen-4.0-generate-001" not in output
    assert "1,048,576" in output
    assert "65,536" in output


def test_list_models_fails_when_pricing_refresh_fails(monkeypatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.models = SimpleNamespace(list=lambda: [])

        def close(self) -> None:
            return None

    def fake_build_client():
        return FakeClient()

    monkeypatch.setattr("pdf_analyzer.main.build_client", fake_build_client)
    monkeypatch.setattr(
        "pdf_analyzer.main.fetch_pricing_snapshot",
        lambda *, client, model_name: (_ for _ in ()).throw(
            PricingSnapshotError("pricing unavailable")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["pdf-analyzer", "--list-models"])

    try:
        main()
    except SystemExit as exc:
        assert str(exc) == "ERROR: Could not refresh Gemini pricing snapshot: pricing unavailable"
    else:
        raise AssertionError("Expected SystemExit")
