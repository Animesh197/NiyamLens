"""Build small synthetic PDFs for extraction/normalization tests.

The text is invented and only imitates the *layout* of RBI directions (A4 pages,
12 pt body text, bold headings, small-font footnotes, page-number footers). It
is not RBI content.
"""

from dataclasses import dataclass

import pymupdf

A4 = (595.32, 841.92)


@dataclass(frozen=True)
class Text:
    x: float
    y: float  # baseline, points from the top
    text: str
    size: float = 12
    bold: bool = False


@dataclass(frozen=True)
class Image:
    x0: float = 72
    y0: float = 72
    x1: float = 300
    y1: float = 300


def build_pdf(pages: list[list[Text | Image]], *, encrypt: bool = False) -> bytes:
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page(width=A4[0], height=A4[1])
        for item in items:
            if isinstance(item, Image):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
                pix.clear_with(200)
                page.insert_image(pymupdf.Rect(item.x0, item.y0, item.x1, item.y1), pixmap=pix)
            else:
                page.insert_text(
                    (item.x, item.y),
                    item.text,
                    fontsize=item.size,
                    fontname="hebo" if item.bold else "helv",
                )
    options = {}
    if encrypt:
        options = {
            "encryption": pymupdf.PDF_ENCRYPT_AES_256,
            "user_pw": "user-test",
            "owner_pw": "owner-test",
        }
    data = doc.tobytes(**options)
    doc.close()
    return data


def centered(y: float, text: str, size: float = 12, bold: bool = True) -> Text:
    width = pymupdf.get_text_length(text, fontname="hebo" if bold else "helv", fontsize=size)
    return Text((A4[0] - width) / 2, y, text, size, bold)


def page_number(n: int) -> Text:
    return Text(295, 790, str(n), 11)


def directions_pdf() -> bytes:
    """A three-page synthetic 'directions' document exercising the normalizer."""
    cover = [
        centered(90, "RESERVE BANK OF INDIA", 14),
        Text(72, 140, "RBI/TEST/2026-27/001"),
        Text(72, 160, "TEST.REF.01/02.03.004/2026-27"),
        centered(200, "Synthetic (Test Entities) Directions, 2026"),
        centered(240, "Table of Contents", 11),
        Text(72, 262, "Chapter I - Preliminary ..................................... 2", 11, True),
        Text(72, 280, "Chapter II - Requirements .................................. 2", 11, True),
    ]
    body1 = [
        centered(70, "Introduction"),
        Text(72, 95, "These synthetic directions exist only to test the parser."),
        centered(125, "Chapter I - Preliminary"),
        Text(72, 150, "A. Short Title and Commencement", bold=True),
        Text(72, 175, "1."),
        Text(100, 175, "These Directions shall be called the Synthetic Directions, 2026."),
        Text(72, 200, "2."),
        Text(100, 200, "These Directions shall not apply to entities with assets below"),
        Text(100, 216, "Rs. 500 crore and shall come into effect on April 1, 2027."),
        centered(245, "Chapter II - Requirements"),
        Text(72, 270, "B. Limits", bold=True),
        Text(72, 295, "3. (1) An entity shall maintain a ratio of at least 15 per cent;"),
        Text(100, 318, "(i) on a standalone basis;"),
        Text(100, 340, "(ii) on a consolidated basis, unless exempted by the Non-"),
        Text(120, 356, "Banking Financial Companies rule;"),
        Text(72, 380, "1", 8),
        Text(78, 384, "[Provided that the ratio may be 12 per cent for new entities.]"),
        Text(72, 410, "(2) An entity must report breaches within 7 days and the"),
        Text(72, 426, "report shall include the amount of the breach and the date on which"),
        Text(72, 742, "1", 6.5),
        Text(78, 746, "Inserted vide Synthetic Amendment Directions, 2026 dated May 5, 2026", 10),
        page_number(2),
    ]
    body2 = [
        Text(72, 70, "it occurred."),
        Text(72, 95, "4. No entity shall lend more than 25% of its capital to one borrower."),
        Text(72, 120, "(a) This applies to all loans;"),
        Text(72, 140, "(b) and to guarantees."),
        Text(72, 175, "(Test Signatory Name)"),
        Text(72, 190, "Chief General Manager"),
        Text(450, 230, "Annex - I", bold=True),
        Text(90, 255, "1. Name of the entity ____"),
        Text(90, 275, "2. Amount ____"),
        page_number(3),
    ]
    return build_pdf([cover, body1, body2])
