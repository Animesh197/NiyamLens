"""Page-by-page PDF text extraction with PyMuPDF (implementationplan.md §5.2).

- Every page is extracted; page numbers are **one-based** (PyMuPDF indexes pages
  from zero, converted here at the extraction boundary). They are the PDF's
  physical page positions, not the page numbers printed in the document footer.
- Text is extracted as-is: no OCR, no de-hyphenation, no clean-up. Lines keep
  their font size and bold flag so the section normalizer can tell headings,
  footnotes and footnote reference markers apart.
- Pages with no or very little text are marked ``needs_manual_review``. Text is
  never invented for them.
- The result is tied to the exact SHA-256 of the PDF bytes that were read.
"""

from datetime import UTC, datetime
from typing import Literal

import pymupdf
from pydantic import BaseModel, Field

from app.ingestion.pdf_downloader import PdfValidationError, sha256_hex, validate_pdf_bytes

# A page with fewer non-whitespace characters than this is flagged for review.
LOW_TEXT_THRESHOLD = 50

# get_text("dict") options: keep ligatures and whitespace as printed, do not
# de-hyphenate, and leave out image bytes (only text is needed here).
_TEXT_FLAGS = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES) & ~pymupdf.TEXT_DEHYPHENATE
_BOLD_FLAG = 16  # PyMuPDF span flag bit for bold fonts


class PdfExtractionError(Exception):
    """The PDF could not be opened or read, or its bytes are not the expected version."""


class TextSpan(BaseModel):
    text: str
    size: float
    bold: bool


class TextLine(BaseModel):
    """One line as PyMuPDF reports it. Coordinates are PDF points, origin top-left."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    spans: list[TextSpan]


class ExtractedPage(BaseModel):
    page_number: int  # one-based physical page position
    width: float
    height: float
    text: str  # PyMuPDF plain text of the page in reading order, unmodified
    lines: list[TextLine]
    char_count: int  # non-whitespace characters in ``text``
    image_count: int
    status: Literal["ok", "needs_manual_review"]
    warnings: list[str] = Field(default_factory=list)


class ExtractorInfo(BaseModel):
    library: str = "PyMuPDF"
    version: str
    mupdf_version: str


class ExtractedDocument(BaseModel):
    sha256: str
    byte_size: int
    page_count: int
    pdf_metadata: dict[str, str | None]
    extractor: ExtractorInfo
    extracted_at: datetime
    pages: list[ExtractedPage]
    warnings: list[str] = Field(default_factory=list)

    @property
    def pages_needing_review(self) -> list[int]:
        return [p.page_number for p in self.pages if p.status == "needs_manual_review"]


def extractor_info() -> ExtractorInfo:
    return ExtractorInfo(version=pymupdf.VersionBind, mupdf_version=pymupdf.VersionFitz)


def _is_bold(span: dict) -> bool:
    return bool(span.get("flags", 0) & _BOLD_FLAG) or "bold" in span.get("font", "").lower()


def _page_lines(page: pymupdf.Page) -> list[TextLine]:
    lines: list[TextLine] = []
    for block in page.get_text("dict", flags=_TEXT_FLAGS, sort=True)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = [
                TextSpan(text=s["text"], size=round(s["size"], 2), bold=_is_bold(s))
                for s in line["spans"]
                if s["text"]
            ]
            text = "".join(s.text for s in spans)
            if not text.strip():
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append(
                TextLine(
                    text=text,
                    x0=round(x0, 2),
                    y0=round(y0, 2),
                    x1=round(x1, 2),
                    y1=round(y1, 2),
                    spans=spans,
                )
            )
    return lines


def _extract_page(page: pymupdf.Page, page_number: int) -> ExtractedPage:
    text = page.get_text("text", flags=_TEXT_FLAGS, sort=True)
    char_count = sum(1 for ch in text if not ch.isspace())
    image_count = len(page.get_image_info())
    warnings: list[str] = []
    status: Literal["ok", "needs_manual_review"] = "ok"
    if char_count == 0:
        status = "needs_manual_review"
        warnings.append(
            "No extractable text"
            + (" (page has images only; it may be scanned)." if image_count else ".")
            + " OCR is not used; review this page manually."
        )
    elif char_count < LOW_TEXT_THRESHOLD:
        status = "needs_manual_review"
        warnings.append(
            f"Suspiciously little text ({char_count} non-whitespace characters); "
            "review this page manually."
        )
    if "�" in text:
        warnings.append("Text contains unreadable characters (U+FFFD); check the page manually.")
    return ExtractedPage(
        page_number=page_number,
        width=round(page.rect.width, 2),
        height=round(page.rect.height, 2),
        text=text,
        lines=_page_lines(page),
        char_count=char_count,
        image_count=image_count,
        status=status,
        warnings=warnings,
    )


def extract_pdf(content: bytes, *, expected_sha256: str | None = None) -> ExtractedDocument:
    """Extract every page of ``content``. Raises PdfExtractionError on any failure."""
    sha = sha256_hex(content)
    if expected_sha256 is not None and sha != expected_sha256:
        raise PdfExtractionError(
            f"PDF bytes have SHA-256 {sha}, expected {expected_sha256}; refusing to extract."
        )
    # PyMuPDF ignores the ``filetype`` hint when the bytes look like another format
    # (it renders HTML, for example), so the PDF signature is checked first.
    try:
        validate_pdf_bytes(content, f"PDF {sha[:12]}")
    except PdfValidationError as exc:
        raise PdfExtractionError(f"Cannot open PDF {sha[:12]}: {exc}") from exc
    pymupdf.TOOLS.mupdf_warnings(reset=True)
    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several exception types for bad files
        raise PdfExtractionError(f"Cannot open PDF {sha[:12]}: {exc}") from exc
    with doc:
        if not doc.is_pdf:
            raise PdfExtractionError(f"Cannot open PDF {sha[:12]}: not opened as a PDF document.")
        if doc.needs_pass or doc.is_encrypted:
            raise PdfExtractionError(f"PDF {sha[:12]} is encrypted; it cannot be extracted.")
        if doc.page_count == 0:
            raise PdfExtractionError(f"PDF {sha[:12]} has no pages.")
        try:
            # PyMuPDF page index is zero-based; NiyamLens page numbers are one-based.
            pages = [_extract_page(page, index + 1) for index, page in enumerate(doc)]
        except Exception as exc:
            raise PdfExtractionError(f"Failed to read PDF {sha[:12]}: {exc}") from exc
        metadata = {k: (v if v else None) for k, v in (doc.metadata or {}).items()}
        page_count = doc.page_count

    warnings = [f"MuPDF: {w}" for w in pymupdf.TOOLS.mupdf_warnings().splitlines() if w.strip()]
    review = [p.page_number for p in pages if p.status == "needs_manual_review"]
    if review:
        warnings.append(f"Pages needing manual review: {review}")
    return ExtractedDocument(
        sha256=sha,
        byte_size=len(content),
        page_count=page_count,
        pdf_metadata=metadata,
        extractor=extractor_info(),
        extracted_at=datetime.now(UTC),
        pages=pages,
        warnings=warnings,
    )
