from pypdf import PdfWriter

from agent.bank_index import BankIndex
from agent.paths import SYNTHETIC_DIR


def test_macro_quote_is_a_source_sentence() -> None:
    index = BankIndex.load(SYNTHETIC_DIR / "bank")
    found = index.macro("key_rate")
    assert found["found"] is True
    assert "14%" in found["value_text"]
    assert any(found["value_text"] in item.text for item in index.fragments)
    missing = index.macro("gdp")
    assert missing["found"] is False
    assert not any(char.isdigit() for char in missing["reason"])
    unknown = index.macro("bitcoin")
    assert unknown["found"] is False


def test_image_pdf_is_not_turned_into_numbers(tmp_path) -> None:
    pdf = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as handle:
        writer.write(handle)
    index = BankIndex.load(tmp_path, cache_dir=tmp_path / "cache")
    assert index.fragments == []
    assert index.unreadable == ["blank.pdf"]
    result = index.macro("key_rate")
    assert result["found"] is False
    assert "14" not in result["reason"]


def test_sidecar_text_is_indexed(tmp_path) -> None:
    pdf = tmp_path / "note.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as handle:
        writer.write(handle)
    pdf.with_suffix(".txt").write_text(
        "Обзор\nКлючевая ставка сохранена на уровне 14%.\n",
        encoding="utf-8",
    )
    index = BankIndex.load(tmp_path)
    assert index.unreadable == []
    assert len(index.fragments) == 1
    found = index.macro("key_rate")
    assert found["found"] is True
    assert found["value_text"] in index.fragments[0].text
