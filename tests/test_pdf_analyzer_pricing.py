from pdf_analyzer.pricing import parse_pricing_page_html


def test_parse_pricing_page_html_extracts_standard_prices() -> None:
    html = """
    <html><body>
      <h2>Gemini 3 Flash Preview</h2>
      <p><code>gemini-3-flash-preview</code></p>
      <h3>Standard</h3>
      <p>Input price Free of charge $0.50 (text / image / video)</p>
      <p>$1.00 (audio)</p>
      <p>Output price (including thinking tokens) Free of charge $3.00</p>
      <h3>Batch</h3>
      <p>Input price Not available $0.25</p>
    </body></html>
    """

    payload = parse_pricing_page_html(html, source_url="https://example.test/pricing")

    assert payload["source_url"] == "https://example.test/pricing"
    assert payload["models"]["gemini-3-flash-preview"]["standard"]["input_usd_per_million_tokens"] == 0.50
    assert payload["models"]["gemini-3-flash-preview"]["standard"]["output_usd_per_million_tokens"] == 3.00
