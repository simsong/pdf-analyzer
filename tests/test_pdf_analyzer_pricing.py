from types import SimpleNamespace

from pdf_analyzer.pricing import fetch_pricing_snapshot


class _FakeHeaders(dict):
    def get_content_type(self) -> str:
        return "text/html"


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers = _FakeHeaders()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_fetch_pricing_snapshot_sends_full_html_to_gemini(monkeypatch) -> None:
    html = "<html><head><style>.x{}</style></head><body><h2>Gemini Test</h2><script>1</script></body></html>"
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "pdf_analyzer.pricing.urlopen",
        lambda request, timeout=30: _FakeResponse(html.encode("utf-8")),
    )

    def fake_extract(*, client, model_name, source_url, source_html):
        observed["client"] = client
        observed["model_name"] = model_name
        observed["source_url"] = source_url
        observed["source_html"] = source_html
        return {
            "object_name": "gemini-prices.google.com",
            "source_url": source_url,
            "pricing_mode": "standard",
            "parsed_by_model": model_name,
            "notes": None,
            "models": {
                "gemini-test": {
                    "display_name": "Gemini Test",
                    "aliases": ["gemini-test"],
                    "standard": {
                        "input_usd_per_million_tokens": 0.5,
                        "output_usd_per_million_tokens": 1.5,
                    },
                    "notes": None,
                }
            },
        }

    monkeypatch.setattr(
        "pdf_analyzer.pricing.extract_pricing_snapshot_with_gemini",
        fake_extract,
    )

    payload, metadata = fetch_pricing_snapshot(
        client=SimpleNamespace(),
        model_name="gemini-3-flash-preview",
        source_url="https://example.test/pricing",
    )

    assert payload["source_url"] == "https://example.test/pricing"
    assert metadata["content_type"] == "text/html"
    assert observed["source_html"] == html
