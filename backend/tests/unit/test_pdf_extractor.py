import hashlib

import pymupdf
import pytest

from app.ingestion.pdf_extractor import PdfExtractionError, extract_pdf
from tests.fixtures.pdf_builder import Image, Text, build_pdf, directions_pdf


def test_pages_are_one_based_and_text_is_kept_verbatim() -> None:
    content = directions_pdf()
    doc = extract_pdf(content, expected_sha256=hashlib.sha256(content).hexdigest())

    assert doc.page_count == 3
    assert [p.page_number for p in doc.pages] == [1, 2, 3]
    assert "Table of Contents" in doc.pages[0].text
    assert "shall not apply to entities with assets below" in doc.pages[1].text
    assert "25% of its capital" in doc.pages[2].text
    assert all(p.status == "ok" for p in doc.pages)
    assert doc.pages_needing_review == []


def test_records_extractor_version_hash_and_size() -> None:
    content = directions_pdf()
    doc = extract_pdf(content)

    assert doc.sha256 == hashlib.sha256(content).hexdigest()
    assert doc.byte_size == len(content)
    assert doc.extractor.library == "PyMuPDF"
    assert doc.extractor.version == pymupdf.VersionBind
    assert doc.extracted_at.tzinfo is not None


def test_lines_keep_font_size_and_bold() -> None:
    doc = extract_pdf(build_pdf([[Text(72, 100, "Heading", 12, True), Text(72, 130, "x", 8)]]))
    lines = {ln.text: ln for ln in doc.pages[0].lines}

    assert lines["Heading"].spans[0].bold is True
    assert lines["x"].spans[0].size == 8
    assert lines["Heading"].y0 < lines["x"].y0


def test_empty_and_image_only_pages_need_manual_review_without_invented_text() -> None:
    body = [Text(72, 100, "A normal page with enough text to pass the low-text threshold easily.")]
    doc = extract_pdf(build_pdf([body, [], [Image()], [Text(72, 100, "Page 4")]]))

    statuses = {p.page_number: p for p in doc.pages}
    assert statuses[1].status == "ok"
    assert statuses[2].status == "needs_manual_review" and statuses[2].text.strip() == ""
    assert "No extractable text." in statuses[2].warnings[0]
    assert statuses[3].status == "needs_manual_review" and statuses[3].image_count == 1
    assert "images only" in statuses[3].warnings[0]
    assert statuses[4].status == "needs_manual_review"
    assert "Suspiciously little text" in statuses[4].warnings[0]
    assert doc.pages_needing_review == [2, 3, 4]
    assert any("[2, 3, 4]" in w for w in doc.warnings)


def test_wrong_version_is_refused() -> None:
    with pytest.raises(PdfExtractionError, match="refusing to extract"):
        extract_pdf(directions_pdf(), expected_sha256="0" * 64)


@pytest.mark.parametrize(
    "content",
    [
        # PyMuPDF would otherwise render HTML as a one-page document.
        b"<!DOCTYPE html><html><body>challenge</body></html>",
        b"",
        b"%PDF-1.7\nnot really a pdf",
    ],
)
def test_not_a_pdf_is_refused(content: bytes) -> None:
    with pytest.raises(PdfExtractionError, match="Cannot open PDF|has no pages"):
        extract_pdf(content)


def test_encrypted_pdf_is_refused() -> None:
    with pytest.raises(PdfExtractionError, match="encrypted"):
        extract_pdf(build_pdf([[Text(72, 100, "secret")]], encrypt=True))
