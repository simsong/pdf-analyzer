from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

from pdf_analyzer.pdfa_tools import analyze_pdf


def _write_pdf(path: Path, root_updates: DictionaryObject | None = None) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if root_updates is not None:
        writer.root_object.update(root_updates)
    with path.open("wb") as handle:
        writer.write(handle)


def test_analyze_pdf_has_no_findings_for_plain_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "plain.pdf"
    _write_pdf(pdf_path)

    analysis = analyze_pdf(pdf_path)

    assert not analysis.requires_pdfa_conversion
    assert analysis.issue_names == ()


def test_analyze_pdf_detects_report_copy_risks(tmp_path: Path) -> None:
    pdf_path = tmp_path / "active.pdf"
    _write_pdf(
        pdf_path,
        DictionaryObject(
            {
                NameObject("/AcroForm"): DictionaryObject({NameObject("/Fields"): ArrayObject()}),
                NameObject("/OpenAction"): DictionaryObject(
                    {
                        NameObject("/S"): NameObject("/JavaScript"),
                        NameObject("/JS"): TextStringObject("app.alert('hello')"),
                    }
                ),
                NameObject("/Names"): DictionaryObject(
                    {
                        NameObject("/EmbeddedFiles"): DictionaryObject(
                            {
                                NameObject("/Names"): ArrayObject(
                                    [
                                        TextStringObject("external.dat"),
                                        DictionaryObject(
                                            {
                                                NameObject("/Type"): NameObject("/Filespec"),
                                                NameObject("/F"): TextStringObject("external.dat"),
                                            }
                                        ),
                                    ]
                                )
                            }
                        )
                    }
                ),
            }
        ),
    )

    analysis = analyze_pdf(pdf_path)

    assert analysis.has_interactive_forms
    assert analysis.has_javascript
    assert analysis.has_external_dependencies
    assert analysis.requires_pdfa_conversion


def test_analyze_pdf_detects_encryption(tmp_path: Path) -> None:
    pdf_path = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("secret")
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    analysis = analyze_pdf(pdf_path)

    assert analysis.is_encrypted
    assert analysis.requires_pdfa_conversion
    assert analysis.issue_names == ("encrypted",)
