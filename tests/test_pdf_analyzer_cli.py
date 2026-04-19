import sys
from types import SimpleNamespace

from pdf_analyzer.exceptions import PricingSnapshotError
from pdf_analyzer.main import main


def test_list_models_prints_available_models_and_prices(monkeypatch, capsys) -> None:
    fake_models = [
        SimpleNamespace(
            name="models/gemini-2.5-flash",
            input_token_limit=1_048_576,
            output_token_limit=65_536,
        ),
        SimpleNamespace(
            name="models/custom-experimental-model",
            input_token_limit=2_000,
            output_token_limit=1_000,
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
                    }
                }
            },
            {},
        ),
    )
    monkeypatch.setattr(sys, "argv", ["analyze", "--list-models"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "MODEL ID" in output
    assert "gemini-2.5-flash" in output
    assert "$0.30" in output
    assert "$2.50" in output
    assert "custom-experimental-model" in output
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
    monkeypatch.setattr(sys, "argv", ["analyze", "--list-models"])

    try:
        main()
    except SystemExit as exc:
        assert str(exc) == "ERROR: Could not refresh Gemini pricing snapshot: pricing unavailable"
    else:
        raise AssertionError("Expected SystemExit")
