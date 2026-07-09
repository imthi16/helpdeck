"""Turn raw sources (PDF bytes, HTML pages, text/markdown) into clean text + metadata."""

import io
import re
from dataclasses import dataclass, field
from typing import Any

import trafilatura
from pypdf import PdfReader


@dataclass
class ExtractedDocument:
    text: str
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ExtractionError(Exception):
    pass


def extract_pdf(data: bytes) -> ExtractedDocument:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"could not parse PDF: {exc}") from exc

    pages: list[dict[str, Any]] = []
    for number, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if page_text:
            pages.append({"page": number, "text": page_text})

    if not pages:
        raise ExtractionError("PDF contains no extractable text")

    title = None
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title).strip() or None

    text = "\n\n".join(page["text"] for page in pages)
    return ExtractedDocument(
        text=text,
        title=title,
        metadata={"page_count": len(reader.pages), "pages": pages},
    )


def extract_html(html: str, url: str | None = None) -> ExtractedDocument:
    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    if not text or not text.strip():
        raise ExtractionError("no main content extracted from HTML")

    doc_meta = trafilatura.extract_metadata(html)
    title = doc_meta.title if doc_meta and doc_meta.title else None

    metadata: dict[str, Any] = {}
    if url:
        metadata["url"] = url
    return ExtractedDocument(text=text.strip(), title=title, metadata=metadata)


_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def extract_text(raw: str) -> ExtractedDocument:
    text = raw.strip()
    if not text:
        raise ExtractionError("document is empty")

    headings = [match.group(1) for match in _MD_HEADING.finditer(text)]
    title = headings[0] if headings else None
    metadata: dict[str, Any] = {"headings": headings} if headings else {}
    return ExtractedDocument(text=text, title=title, metadata=metadata)
