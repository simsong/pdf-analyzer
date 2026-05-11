from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

from pdf_analyzer.pdfa_tools import analyze_pdf, fix_pdf_in_place, main


def _write_pdf(path: Path, root_updates: DictionaryObject | None = None) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if root_updates is not None:
        writer.root_object.update(root_updates)
    with path.open("wb") as handle:
        writer.write(handle)


def test_analyze_pdf_requires_normalization_for_non_pdfa_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "plain.pdf"
    _write_pdf(pdf_path)

    analysis = analyze_pdf(pdf_path)

    assert not analysis.is_pdfa_valid
    assert analysis.requires_normalization
    assert analysis.issue_names == ("not_pdfa",)


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
    assert analysis.requires_normalization
    assert analysis.issue_names == ("not_pdfa", "encrypted")


def test_analyze_pdf_detects_cross_domain_policy_triggers(tmp_path: Path) -> None:
    pdf_path = tmp_path / "policy.pdf"
    _write_pdf(
        pdf_path,
        DictionaryObject(
            {
                NameObject("/AcroForm"): DictionaryObject(
                    {
                        NameObject("/Fields"): ArrayObject(
                            [
                                DictionaryObject(
                                    {
                                        NameObject("/FT"): NameObject("/Sig"),
                                        NameObject("/T"): TextStringObject("Signature1"),
                                    }
                                )
                            ]
                        )
                    }
                ),
                NameObject("/OpenAction"): DictionaryObject(
                    {
                        NameObject("/S"): NameObject("/Launch"),
                        NameObject("/F"): TextStringObject("calc.exe"),
                    }
                ),
                NameObject("/Names"): DictionaryObject(
                    {
                        NameObject("/EmbeddedFiles"): DictionaryObject(
                            {NameObject("/Names"): ArrayObject([TextStringObject("payload.bin")])}
                        )
                    }
                ),
                NameObject("/AA"): DictionaryObject(
                    {
                        NameObject("/D"): DictionaryObject(
                            {
                                NameObject("/S"): NameObject("/URI"),
                                NameObject("/URI"): TextStringObject("https://example.test/"),
                            }
                        ),
                        NameObject("/U"): DictionaryObject(
                            {NameObject("/S"): NameObject("/Rendition")}
                        ),
                    }
                ),
            }
        ),
    )

    analysis = analyze_pdf(pdf_path)

    assert analysis.has_digital_signatures
    assert analysis.has_embedded_files
    assert analysis.has_launch_actions
    assert analysis.has_multimedia
    assert analysis.has_uri_actions
    assert analysis.has_interactive_forms
    assert analysis.requires_normalization


def test_fix_pdf_in_place_converts_active_pdf(tmp_path: Path) -> None:
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
            }
        ),
    )

    result = fix_pdf_in_place(pdf_path)

    assert result.converted
    assert result.error is None
    assert not analyze_pdf(pdf_path).requires_normalization


def test_pdfa_fix_cli_prints_only_converted_and_non_pdf_paths(tmp_path: Path, capsys) -> None:
    pdf_path = tmp_path / "plain.pdf"
    text_path = tmp_path / "notes.txt"
    active_path = tmp_path / "active.pdf"
    _write_pdf(pdf_path)
    assert fix_pdf_in_place(pdf_path).converted
    text_path.write_text("not a PDF\n", encoding="utf-8")
    _write_pdf(
        active_path,
        DictionaryObject(
            {
                NameObject("/OpenAction"): DictionaryObject(
                    {
                        NameObject("/S"): NameObject("/JavaScript"),
                        NameObject("/JS"): TextStringObject("app.alert('hello')"),
                    }
                ),
            }
        ),
    )

    exit_code = main([str(pdf_path), str(text_path), str(active_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out.splitlines() == [str(text_path), str(active_path)]
