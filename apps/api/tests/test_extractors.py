from pathlib import Path

import pytest

from app.services.ingestion.extractors import (
    ExtractionError,
    extract_html,
    extract_pdf,
    extract_text,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestExtractPdf:
    def test_extracts_text_and_page_numbers(self) -> None:
        doc = extract_pdf((FIXTURES / "sample.pdf").read_bytes())

        assert "Fill the water tank" in doc.text
        assert "Descale the machine" in doc.text
        assert doc.metadata["page_count"] == 2
        pages = doc.metadata["pages"]
        assert [p["page"] for p in pages] == [1, 2]
        assert "Getting Started" in pages[0]["text"]
        assert "Cleaning" in pages[1]["text"]

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ExtractionError):
            extract_pdf(b"not a pdf at all")


class TestExtractHtml:
    def test_strips_boilerplate_keeps_content(self) -> None:
        html = (FIXTURES / "sample.html").read_text()
        doc = extract_html(html, url="https://acme.example/shipping")

        assert "dispatched the same day" in doc.text.replace("\n", " ")
        assert "75 euros" in doc.text
        # nav/footer boilerplate must be stripped
        assert "Privacy" not in doc.text
        assert "Home" not in doc.text
        assert doc.title == "Shipping Policy"
        assert doc.metadata["url"] == "https://acme.example/shipping"

    def test_rejects_contentless_html(self) -> None:
        with pytest.raises(ExtractionError):
            extract_html("<html><body></body></html>")


class TestExtractText:
    def test_markdown_passthrough_with_headings(self) -> None:
        raw = (FIXTURES / "sample.md").read_text()
        doc = extract_text(raw)

        assert doc.text == raw.strip()
        assert doc.title == "Returns and Refunds"
        assert doc.metadata["headings"] == [
            "Returns and Refunds",
            "Conditions",
            "How to start a return",
        ]

    def test_rejects_empty(self) -> None:
        with pytest.raises(ExtractionError):
            extract_text("   \n  ")
