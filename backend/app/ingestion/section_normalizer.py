"""Section and clause normalization (implementationplan.md §5.3).

Turns extracted lines into ordered units:

- ``paragraph`` / ``subparagraph``: numbered RBI clauses such as ``3``, ``3(1)``
  and ``3(1)(i)``, with their chapter and section heading;
- ``footnote``: amendment footnotes printed at the bottom of a page;
- ``unstructured_page_block``: text with no reliable paragraph boundary
  (front matter and table of contents, introductions, signature blocks and
  annexes such as reporting templates). These are never given a clause number.

Every unit keeps ``original_text`` (the extracted lines, unchanged, joined by
newlines) and a separate ``normalized_text`` for comparison. Normalization only
standardizes Unicode variants, dashes, quotes, line breaks and whitespace, and
removes footnote *reference* markers (identified by their small font size, never
by pattern alone). A token check confirms that no number, negation or modal verb
was lost; a unit failing it is flagged for review. Page furniture (printed page
numbers and repeated running headers) is excluded from units but stays in the
page text kept by the extractor.

Numbering is accepted only when it continues the expected sequence. Anything
unexpected is kept as text and flagged, never guessed.
"""

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.ingestion.pdf_extractor import ExtractedDocument, TextSpan

NORMALIZER_VERSION = "1"

UnitType = Literal["paragraph", "subparagraph", "footnote", "unstructured_page_block"]
BlockKind = Literal["front_matter", "preamble", "signature", "annex", "unattached"]

# -- patterns ------------------------------------------------------------------
TOC_RE = re.compile(r"(?:\.\s*){4,}\s*\d{1,3}\s*$")
CHAPTER_RE = re.compile(r"^Chapter\s*[-–—]?\s*([IVXLC]+)\b\s*[-–—:]?\s*(.*)$", re.IGNORECASE)
SECTION_RE = re.compile(r"^([A-Z])\.\s*(\S.*)$")
ANNEX_RE = re.compile(r"^Annex(?:ure)?\s*[-–—]?\s*([IVXLC]+|\d{1,2}|[A-Z])?\s*$", re.IGNORECASE)
PARA_RE = re.compile(r"^(\d{1,3})\.(?:\s+|$)")
SUB_RE = re.compile(r"^\(\s*(\d{1,3}|[ivxlc]{1,6}|[a-z])\s*\)(?:\s+|$)")
SIGNATURE_RE = re.compile(r"^\(\s*[A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*)+\s*\)$")
PAGE_NUMBER_RE = re.compile(r"^\s*(?:-\s*)?\d{1,3}(?:\s*-)?\s*$")
FOOTNOTE_START_RE = re.compile(r"^\s*(\d{1,3})\b")
INTRO_HEADINGS = {"introduction", "preamble"}
# "Provided that", "Explanation:", "Note:", optionally after an amendment marker like "1[".
PROVISO_RE = re.compile(r"^\W*\d*\W*(Provided|Explanation|Note)\b")
ATTACHMENT_FLAG = "attachment_uncertain:unnumbered_proviso_or_note"

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
_QUOTES = {
    **dict.fromkeys(map(ord, "‘’‚‛′"), "'"),
    **dict.fromkeys(map(ord, "“”„‟″"), '"'),
}
_SPACES = dict.fromkeys(map(ord, "   ​﻿"), " ")

# Tokens that must survive normalization unchanged (plan §5.3).
NUMBER_TOKEN_RE = re.compile(r"\d+")
PROTECTED_WORDS = frozenset(
    {
        "not", "no", "nor", "never", "neither", "none", "cannot", "without", "except",
        "unless", "notwithstanding", "shall", "must", "may", "should", "will", "would",
        "can", "could", "might", "per", "cent", "percent",
    }
)  # fmt: skip
WORD_RE = re.compile(r"[a-z]+")

_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def roman_value(text: str) -> int | None:
    text = text.lower()
    if not text or any(ch not in _ROMAN for ch in text):
        return None
    total = 0
    for i, ch in enumerate(text):
        value = _ROMAN[ch]
        total += -value if i + 1 < len(text) and _ROMAN[text[i + 1]] > value else value
    return total if _to_roman(total) == text else None


def _to_roman(number: int) -> str:
    out = ""
    for value, symbol in (
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ):  # noqa: E501
        while number >= value:
            out += symbol
            number -= value
    return out


# -- text normalization ----------------------------------------------------------
def normalize_text(text: str) -> str:
    """Comparison form of one line: NFKC, unified dashes/quotes/spaces, collapsed spaces."""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES)
    return re.sub(r"\s+", " ", text.translate(_SPACES)).strip()


def join_lines(lines: list[str]) -> str:
    """Join normalized lines. A line ending in a hyphen after a letter is joined without a
    space and keeps the hyphen (``Non-`` + ``Banking`` -> ``Non-Banking``)."""
    out = ""
    for line in lines:
        if not line:
            continue
        if out and len(out) >= 2 and out.endswith("-") and out[-2].isalpha() and line[0].isalnum():
            out += line
        else:
            out = f"{out} {line}" if out else line
    return out


def protected_tokens(text: str) -> Counter:
    text = unicodedata.normalize("NFKC", text).lower()
    return Counter(NUMBER_TOKEN_RE.findall(text)) + Counter(
        w for w in WORD_RE.findall(text) if w in PROTECTED_WORDS
    )


# -- models ----------------------------------------------------------------------
class SectionUnit(BaseModel):
    sequence: int
    unit_type: UnitType
    block_kind: BlockKind | None = None
    section_identifier: str
    paragraph_number: str | None = None
    subparagraph_path: list[str] = Field(default_factory=list)
    chapter: str | None = None
    heading: str | None = None
    page_start: int  # one-based
    page_end: int  # one-based
    original_text: str
    normalized_text: str
    content_hash: str  # SHA-256 of normalized_text
    footnote_markers: list[str] = Field(default_factory=list)
    extraction_status: Literal["ok", "needs_review"]
    flags: list[str] = Field(default_factory=list)


class OutlineEntry(BaseModel):
    kind: Literal["chapter", "heading", "annex"]
    text: str
    page: int


class NormalizedDocument(BaseModel):
    sha256: str
    normalizer_version: str = NORMALIZER_VERSION
    body_font_size: float
    outline: list[OutlineEntry]
    units: list[SectionUnit]
    warnings: list[str] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for unit in self.units:
            out[unit.unit_type] = out.get(unit.unit_type, 0) + 1
        out["needs_review"] = sum(u.extraction_status == "needs_review" for u in self.units)
        return out


# -- internal row model ------------------------------------------------------------
@dataclass
class _Row:
    page: int
    y0: float
    y1: float
    x0: float
    x1: float
    page_width: float
    page_height: float
    spans: list[TextSpan]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def stripped(self) -> str:
        return normalize_text(self.text)

    @property
    def max_size(self) -> float:
        return max((s.size for s in self.spans if s.text.strip()), default=0.0)

    @property
    def all_bold(self) -> bool:
        visible = [s for s in self.spans if s.text.strip()]
        return bool(visible) and all(s.bold for s in visible)

    @property
    def centered(self) -> bool:
        middle = (self.x0 + self.x1) / 2
        return self.x0 > 90 and abs(middle - self.page_width / 2) < 0.08 * self.page_width


@dataclass
class _Open:
    unit_type: UnitType
    identifier: str
    rows: list[_Row] = field(default_factory=list)
    block_kind: BlockKind | None = None
    paragraph_number: str | None = None
    path: list[str] = field(default_factory=list)
    chapter: str | None = None
    heading: str | None = None
    flags: list[str] = field(default_factory=list)
    marker: str | None = None  # footnote number


def _rows(document: ExtractedDocument) -> list[_Row]:
    """Merge lines that share a baseline (e.g. ``1.`` and its text) into one row."""
    rows: list[_Row] = []
    for page in document.pages:
        lines = sorted(page.lines, key=lambda ln: (ln.y0, ln.x0))
        page_rows: list[_Row] = []
        for line in lines:
            row = next(
                (
                    r
                    for r in page_rows
                    if abs(r.y0 - line.y0) <= 3.0 and min(r.y1, line.y1) - max(r.y0, line.y0) > 0
                ),
                None,
            )
            if row is None:
                page_rows.append(
                    _Row(
                        page.page_number,
                        line.y0,
                        line.y1,
                        line.x0,
                        line.x1,
                        page.width,
                        page.height,
                        list(line.spans),
                    )
                )
                continue
            # Insert in left-to-right order with a single separating space.
            before = line.x0 < row.x0
            joiner = TextSpan(text=" ", size=line.spans[0].size, bold=line.spans[0].bold)
            if before:
                row.spans = [*line.spans, joiner, *row.spans]
            else:
                row.spans = [*row.spans, joiner, *line.spans]
            row.x0, row.x1 = min(row.x0, line.x0), max(row.x1, line.x1)
            row.y1 = max(row.y1, line.y1)
        rows.extend(sorted(page_rows, key=lambda r: (r.y0, r.x0)))
    return rows


def _body_font_size(document: ExtractedDocument) -> float:
    weights: Counter = Counter()
    for page in document.pages:
        for line in page.lines:
            for span in line.spans:
                weights[round(span.size * 2) / 2] += len(span.text.strip())
    return weights.most_common(1)[0][0] if weights else 0.0


def _furniture(rows: list[_Row], page_count: int) -> set[int]:
    """Indexes of printed page numbers (top or bottom margin) and running headers.

    Running headers are text repeated in the top margin on many pages. Repeated
    text at the bottom is *not* treated as furniture, because amendment footnotes
    there often repeat the same wording.
    """

    def top(row: _Row) -> bool:
        return row.y1 < 0.08 * row.page_height

    def bottom(row: _Row) -> bool:
        return row.y0 > 0.9 * row.page_height

    header_text = Counter(row.stripped for row in rows if top(row))
    repeated = {t for t, n in header_text.items() if n >= max(3, page_count // 3)}
    out: set[int] = set()
    for i, row in enumerate(rows):
        if (top(row) or bottom(row)) and PAGE_NUMBER_RE.match(row.text):
            out.add(i)
        elif top(row) and row.stripped in repeated:
            out.add(i)
    return out


def _footnotes(rows: list[_Row], skip: set[int], body_size: float) -> set[int]:
    """Indexes of rows in the small-font block at the bottom of each page."""
    out: set[int] = set()
    by_page: dict[int, list[int]] = {}
    for i, row in enumerate(rows):
        if i not in skip:
            by_page.setdefault(row.page, []).append(i)
    for indexes in by_page.values():
        for i in sorted(indexes, key=lambda k: rows[k].y0, reverse=True):
            row = rows[i]
            if row.max_size <= body_size - 1.0 and row.y0 > 0.6 * row.page_height:
                out.add(i)
            else:
                break
    return out


def _is_reference_marker(span: TextSpan, body_size: float) -> bool:
    return span.size <= 0.8 * body_size and span.text.strip().isdigit()


class _Builder:
    def __init__(self, document: ExtractedDocument) -> None:
        self.document = document
        self.body_size = _body_font_size(document)
        self.units: list[SectionUnit] = []
        self._starts: list[tuple[int, float, float]] = []  # (page, y0, x0) of each unit's first row
        self.outline: list[OutlineEntry] = []
        self.warnings: list[str] = []
        self.review_pages = set(document.pages_needing_review)

    # -- unit output -----------------------------------------------------------
    def emit(self, unit: _Open | None) -> None:
        if unit is None or not unit.rows:
            return
        original_lines: list[str] = []
        normalized_lines: list[str] = []
        check_lines: list[str] = []
        markers: list[str] = []
        for row in unit.rows:
            original_lines.append(row.text)
            kept: list[str] = []
            for span in row.spans:
                if unit.unit_type != "footnote" and _is_reference_marker(span, self.body_size):
                    markers.append(span.text.strip())
                else:
                    kept.append(span.text)
            normalized_lines.append(normalize_text("".join(kept)))
            check_lines.append("".join(kept))
        normalized = join_lines(normalized_lines)
        flags = list(unit.flags)
        if protected_tokens("\n".join(check_lines)) != protected_tokens(normalized):
            flags.append("normalization_check_failed")
        pages = sorted({r.page for r in unit.rows})
        if self.review_pages.intersection(pages):
            flags.append("page_needs_manual_review")
        if unit.unit_type == "unstructured_page_block":
            flags.append("no_reliable_paragraph_boundary")
        review_flags = {f for f in flags if not f.startswith("info:")}
        first = unit.rows[0]
        self._starts.append((first.page, first.y0, first.x0))
        self.units.append(
            SectionUnit(
                sequence=0,  # set in reading order once all units are known
                unit_type=unit.unit_type,
                block_kind=unit.block_kind,
                section_identifier=unit.identifier,
                paragraph_number=unit.paragraph_number,
                subparagraph_path=list(unit.path),
                chapter=unit.chapter,
                heading=unit.heading,
                page_start=pages[0],
                page_end=pages[-1],
                original_text="\n".join(original_lines),
                normalized_text=normalized,
                content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                footnote_markers=markers,
                extraction_status="needs_review" if review_flags else "ok",
                flags=flags,
            )
        )

    # -- main pass ---------------------------------------------------------------
    def build(self) -> NormalizedDocument:
        rows = _rows(self.document)
        furniture = _furniture(rows, self.document.page_count)
        footnote_rows = _footnotes(rows, furniture, self.body_size)
        toc_pages = {r.page for r in rows if TOC_RE.search(r.stripped)}

        chapter: str | None = None
        heading: str | None = None
        heading_target: Literal["chapter", "heading"] | None = None
        body_started = False
        mode: BlockKind | None = "front_matter"
        annex_label: str | None = None
        last_paragraph = 0
        paragraph: str | None = None
        stack: list[tuple[str, str]] = []  # (level kind, marker) e.g. ("num", "1")
        current: _Open | None = None
        block_counter = 0
        footnote: _Open | None = None
        footnote_ids: Counter = Counter()

        def new_block(kind: BlockKind, row: _Row) -> _Open:
            nonlocal block_counter
            block_counter += 1
            return _Open(
                unit_type="unstructured_page_block",
                identifier=f"unstructured:{kind}:p{row.page}:{block_counter}",
                block_kind=kind,
                chapter=chapter,
                heading=annex_label if kind == "annex" else heading,
            )

        def add_to_block(kind: BlockKind, row: _Row) -> None:
            nonlocal current
            if not (
                current is not None
                and current.unit_type == "unstructured_page_block"
                and current.block_kind == kind
                and current.rows
                and current.rows[-1].page == row.page
            ):
                self.emit(current)
                current = new_block(kind, row)
            current.rows.append(row)

        def flush_footnote() -> None:
            nonlocal footnote
            self.emit(footnote)
            footnote = None

        for i, row in enumerate(rows):
            text = row.stripped
            if i in furniture or not text:
                continue

            # Footnotes are separate units; they never interrupt the clause they sit under.
            if i in footnote_rows and body_started and mode != "annex":
                first = next((s for s in row.spans if s.text.strip()), None)
                start = FOOTNOTE_START_RE.match(row.text)
                is_start = bool(start) and first is not None and first.size < row.max_size - 0.5
                if is_start or footnote is None or footnote.rows[-1].page != row.page:
                    flush_footnote()
                    marker = start.group(1) if is_start else None
                    ident = f"footnote:{marker}" if marker else f"footnote:p{row.page}"
                    footnote_ids[ident] += 1
                    if footnote_ids[ident] > 1:
                        ident = f"{ident}:p{row.page}:{footnote_ids[ident]}"
                    footnote = _Open(
                        unit_type="footnote",
                        identifier=ident,
                        marker=marker,
                        chapter=chapter,
                        heading=heading,
                        flags=[] if is_start else ["footnote_without_marker"],
                    )
                footnote.rows.append(row)
                continue
            flush_footnote()

            heading_size_ok = row.max_size >= self.body_size - 0.5
            is_chapter = (
                row.all_bold
                and heading_size_ok
                and CHAPTER_RE.match(text)
                and not TOC_RE.search(text)
            )
            is_intro = row.all_bold and row.centered and text.lower() in INTRO_HEADINGS

            # Front matter (cover, identifiers, title, table of contents).
            if not body_started:
                if (is_chapter or is_intro) and row.page not in toc_pages:
                    body_started = True
                else:
                    # Text after the table of contents but before Chapter I (an
                    # untitled introduction) is preamble, not cover/front matter.
                    after_toc = bool(toc_pages) and row.page > max(toc_pages)
                    add_to_block("preamble" if after_toc else "front_matter", row)
                    continue

            # Annexes: kept as unstructured page blocks until the end of the document.
            if row.all_bold and ANNEX_RE.match(text):
                self.emit(current)
                current = None
                mode, annex_label = "annex", text
                self.outline.append(OutlineEntry(kind="annex", text=text, page=row.page))
                add_to_block("annex", row)
                continue
            if mode == "annex":
                if is_chapter:
                    self.warnings.append(f"Chapter heading after an annex on page {row.page}.")
                add_to_block("annex", row)
                continue

            if is_chapter:
                self.emit(current)
                current, mode = None, None
                chapter, heading, heading_target = text, None, "chapter"
                stack, paragraph = [], None
                self.outline.append(OutlineEntry(kind="chapter", text=text, page=row.page))
                continue
            if (
                heading_target
                and row.all_bold
                and current is None
                and not SECTION_RE.match(text)
                and not PARA_RE.match(text)
                and not SUB_RE.match(text)
            ):
                # A chapter or section heading that wraps onto a second line.
                if heading_target == "chapter":
                    chapter = f"{chapter} {text}"
                    self.outline[-1].text = chapter
                else:
                    heading = f"{heading} {text}"
                    self.outline[-1].text = heading
                continue
            heading_target = None
            if (
                row.all_bold
                and heading_size_ok
                and (
                    SECTION_RE.match(text)
                    or (row.centered and len(text) < 100 and not text.endswith((".", ";", ",")))
                )
            ):
                self.emit(current)
                current, mode = None, None
                heading, heading_target = text, "heading"
                stack, paragraph = [], None
                self.outline.append(OutlineEntry(kind="heading", text=text, page=row.page))
                continue

            if mode == "signature" and not PARA_RE.match(text):
                add_to_block("signature", row)
                continue
            if SIGNATURE_RE.match(text) and not SUB_RE.match(text):
                mode = "signature"
                add_to_block("signature", row)
                continue

            # Numbered paragraphs and sub-paragraphs.
            rest = text
            new_paragraph: str | None = None
            para_match = PARA_RE.match(rest)
            if para_match:
                number = int(para_match.group(1))
                if number > last_paragraph:
                    new_paragraph = str(number)
                    gap = number != last_paragraph + 1
                    rest = rest[para_match.end() :]
                elif current is not None:
                    current.flags.append(f"unexpected_paragraph_number:{number}")
            sub_markers: list[tuple[str, str]] = []
            probe_stack = [] if new_paragraph else list(stack)
            while True:
                sub_match = SUB_RE.match(rest)
                if not sub_match:
                    break
                level = _classify(sub_match.group(1), probe_stack)
                if level is None:
                    if current is not None and not new_paragraph and not sub_markers:
                        current.flags.append(f"unexpected_list_marker:({sub_match.group(1)})")
                    break
                kind, marker = level
                while (
                    probe_stack
                    and probe_stack[-1][0] != kind
                    and kind in {k for k, _ in probe_stack}
                ):
                    probe_stack.pop()
                if probe_stack and probe_stack[-1][0] == kind:
                    probe_stack[-1] = (kind, marker)
                else:
                    probe_stack.append((kind, marker))
                sub_markers.append((kind, marker))
                rest = rest[sub_match.end() :]

            if new_paragraph or sub_markers:
                if new_paragraph:
                    last_paragraph = int(new_paragraph)
                    paragraph = new_paragraph
                if paragraph is None:
                    # A list item before any numbered paragraph: keep as text, do not invent.
                    if current is None:
                        current = new_block("unattached", row)
                    current.flags.append("list_marker_outside_paragraph")
                    current.rows.append(row)
                    continue
                self.emit(current)
                stack = probe_stack
                path = [f"({m})" for _, m in stack]
                current = _Open(
                    unit_type="subparagraph" if path else "paragraph",
                    identifier=paragraph + "".join(path),
                    paragraph_number=paragraph,
                    path=path,
                    chapter=chapter,
                    heading=heading,
                )
                if new_paragraph and gap:
                    current.flags.append(f"paragraph_number_gap:after_{int(paragraph) - 1}")
                if len(sub_markers) > 1 or (new_paragraph and sub_markers):
                    current.flags.append("info:combined_markers_on_one_line")
                current.rows.append(row)
                mode = None
                continue

            # Continuation text.
            if current is not None and current.unit_type != "unstructured_page_block":
                if (
                    current.unit_type == "subparagraph"
                    and PROVISO_RE.match(text)
                    and ATTACHMENT_FLAG not in current.flags
                ):
                    # An unnumbered proviso/explanation/note after a list item may
                    # belong to the parent clause instead; do not guess.
                    current.flags.append(ATTACHMENT_FLAG)
                current.rows.append(row)
            elif current is not None and current.block_kind in {"preamble", "unattached"}:
                add_to_block(current.block_kind, row)
            else:
                add_to_block("preamble", row)

        flush_footnote()
        self.emit(current)

        # Reading order: by where each unit starts. A footnote finishes before the
        # clause above it that continues on the next page, but starts after it.
        order = sorted(range(len(self.units)), key=lambda k: self._starts[k])
        self.units = [
            self.units[k].model_copy(update={"sequence": n}) for n, k in enumerate(order, 1)
        ]

        if not any(u.unit_type == "paragraph" or u.unit_type == "subparagraph" for u in self.units):
            self.warnings.append("No numbered paragraphs were recognised; review the structure.")
        if not any(e.kind == "chapter" for e in self.outline):
            self.warnings.append("No chapter headings were recognised.")
        return NormalizedDocument(
            sha256=self.document.sha256,
            body_font_size=self.body_size,
            outline=self.outline,
            units=self.units,
            warnings=self.warnings,
        )


def _classify(marker: str, stack: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Return (level kind, marker) if ``marker`` validly continues or starts a list, else None.

    Kinds: ``num`` (1), ``roman`` (i), ``alpha`` (a). Letters such as ``i``, ``v``, ``x`` and
    ``c`` are both roman numerals and letters; the surrounding list decides which.
    """
    levels = dict(stack)  # kind -> last marker at that level
    if marker.isdigit():
        candidates = ["num"]
    else:
        candidates = []
        alpha_ok = len(marker) == 1
        roman_ok = roman_value(marker) is not None
        prev_alpha, prev_roman = levels.get("alpha"), levels.get("roman")
        if alpha_ok and prev_alpha and ord(marker) == ord(prev_alpha) + 1:
            candidates.append("alpha")
        if roman_ok and prev_roman and roman_value(marker) == roman_value(prev_roman) + 1:
            candidates.append("roman")
        if not candidates:
            if roman_ok and marker == "i":
                candidates.append("roman")
            elif alpha_ok and marker == "a":
                candidates.append("alpha")
    for kind in candidates:
        previous = levels.get(kind)
        if previous is None:
            first = {"num": "1", "roman": "i", "alpha": "a"}[kind]
            if marker == first:
                return kind, marker
            continue
        if kind == "num" and int(marker) == int(previous) + 1:
            return kind, marker
        if kind == "alpha" and ord(marker) == ord(previous) + 1:
            return kind, marker
        if kind == "roman" and roman_value(marker) == (roman_value(previous) or 0) + 1:
            return kind, marker
    return None


def normalize_document(document: ExtractedDocument) -> NormalizedDocument:
    return _Builder(document).build()
