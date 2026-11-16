from collections import Counter

import pytest

from app.ingestion.pdf_extractor import extract_pdf
from app.ingestion.section_normalizer import (
    ATTACHMENT_FLAG,
    join_lines,
    normalize_document,
    normalize_text,
    protected_tokens,
    roman_value,
)
from tests.fixtures.pdf_builder import Text, build_pdf, centered, directions_pdf, page_number


@pytest.fixture(scope="module")
def units() -> dict:
    doc = normalize_document(extract_pdf(directions_pdf()))
    return {u.section_identifier: u for u in doc.units}


def numbered(units: dict) -> list[str]:
    return [k for k, u in units.items() if u.unit_type in {"paragraph", "subparagraph"}]


def test_hierarchy_identifiers_in_reading_order(units: dict) -> None:
    assert numbered(units) == ["1", "2", "3(1)", "3(1)(i)", "3(1)(ii)", "3(2)", "4", "4(a)", "4(b)"]
    sequences = [u.sequence for u in units.values()]
    assert sorted(sequences) == list(range(1, len(units) + 1))
    # The footnote at the bottom of page 2 comes after 3(2), which starts above it.
    assert units["footnote:1"].sequence == units["3(2)"].sequence + 1


def test_chapter_heading_and_one_based_pages(units: dict) -> None:
    assert units["1"].chapter == "Chapter I - Preliminary"
    assert units["1"].heading == "A. Short Title and Commencement"
    assert units["3(1)(i)"].chapter == "Chapter II - Requirements"
    assert units["3(1)(i)"].heading == "B. Limits"
    assert (units["1"].page_start, units["1"].page_end) == (2, 2)
    # A clause that continues on the next page keeps both page references.
    assert (units["3(2)"].page_start, units["3(2)"].page_end) == (2, 3)
    assert units["3(2)"].normalized_text.endswith("the date on which it occurred.")


def test_negation_numbers_and_dates_are_preserved(units: dict) -> None:
    text = units["2"].normalized_text
    assert "shall not apply" in text and "Rs. 500 crore" in text and "April 1, 2027" in text
    assert "25% of its capital" in units["4"].normalized_text
    assert "No entity shall lend" in units["4"].normalized_text
    assert "at least 15 per cent" in units["3(1)"].normalized_text
    assert "within 7 days" in units["3(2)"].normalized_text
    assert all(u.extraction_status == "ok" for k, u in units.items() if k in {"1", "2", "4"})
    assert not any("normalization_check_failed" in u.flags for u in units.values())


def test_original_text_is_kept_separately(units: dict) -> None:
    unit = units["3(1)(ii)"]
    assert unit.original_text.splitlines()[0].startswith("(ii) on a consolidated basis")
    assert "Non-\n" in unit.original_text  # line break as extracted
    assert "Non-Banking Financial Companies" in unit.normalized_text  # hyphen kept, no space


def test_footnote_reference_marker_and_footnote_unit(units: dict) -> None:
    unit = units["3(1)(ii)"]
    assert unit.footnote_markers == ["1"]
    assert "1[Provided" in unit.original_text.replace(" ", "")  # marker stays in the original
    assert "[Provided that the ratio may be 12 per cent" in unit.normalized_text  # removed here
    footnote = units["footnote:1"]
    assert footnote.unit_type == "footnote" and footnote.page_start == 2
    assert footnote.normalized_text.startswith("1 Inserted vide Synthetic Amendment Directions")


def test_unnumbered_proviso_after_list_item_is_flagged(units: dict) -> None:
    unit = units["3(1)(ii)"]
    assert ATTACHMENT_FLAG in unit.flags
    assert unit.extraction_status == "needs_review"


def test_page_numbers_and_furniture_are_not_in_units(units: dict) -> None:
    for unit in units.values():
        assert unit.original_text.strip() not in {"2", "3"}
        assert not unit.original_text.rstrip().endswith("\n2")


def test_unstructured_blocks_are_never_numbered(units: dict) -> None:
    kinds = {u.block_kind: u for u in units.values() if u.unit_type == "unstructured_page_block"}
    assert set(kinds) == {"front_matter", "preamble", "signature", "annex"}
    for unit in kinds.values():
        assert unit.paragraph_number is None
        assert "no_reliable_paragraph_boundary" in unit.flags
        assert unit.extraction_status == "needs_review"
    assert "Table of Contents" in kinds["front_matter"].original_text
    assert kinds["preamble"].heading == "Introduction"
    assert kinds["signature"].normalized_text == "(Test Signatory Name) Chief General Manager"
    # Numbered lines inside the annex template stay in the annex block.
    assert "1. Name of the entity" in kinds["annex"].normalized_text


def test_outline(units: dict) -> None:
    doc = normalize_document(extract_pdf(directions_pdf()))
    assert [(e.kind, e.text, e.page) for e in doc.outline] == [
        ("heading", "Introduction", 2),
        ("chapter", "Chapter I - Preliminary", 2),
        ("heading", "A. Short Title and Commencement", 2),
        ("chapter", "Chapter II - Requirements", 2),
        ("heading", "B. Limits", 2),
        ("annex", "Annex - I", 3),
    ]
    assert doc.warnings == []


# -- sequence and ambiguity handling -------------------------------------------------
def body(*lines: Text) -> bytes:
    return build_pdf(
        [
            [
                centered(70, "Chapter I - General"),
                *lines,
                page_number(1),
            ]
        ]
    )


def by_id(content: bytes) -> dict:
    return {u.section_identifier: u for u in normalize_document(extract_pdf(content)).units}


def test_out_of_sequence_number_is_kept_as_text_and_flagged() -> None:
    result = by_id(
        body(
            Text(72, 100, "1. First paragraph that refers to the rule in paragraph"),
            Text(72, 120, "1. of the other directions."),
            Text(72, 140, "2. Second paragraph."),
        )
    )
    assert numbered(result) == ["1", "2"]
    assert "1. of the other directions." in result["1"].normalized_text
    assert "unexpected_paragraph_number:1" in result["1"].flags
    assert result["1"].extraction_status == "needs_review"


def test_paragraph_number_gap_is_flagged() -> None:
    result = by_id(body(Text(72, 100, "1. First."), Text(72, 120, "3. Third.")))
    assert numbered(result) == ["1", "3"]
    assert "paragraph_number_gap:after_2" in result["3"].flags


def test_roman_and_alphabetic_markers_are_disambiguated_by_context() -> None:
    letters = "abcdefgh"
    lines = [Text(72, 100, "1. Items:")]
    lines += [Text(90, 120 + 18 * k, f"({ch}) item {ch};") for k, ch in enumerate(letters)]
    lines += [Text(90, 120 + 18 * 8, "(i) item after h is the letter i;")]
    lines += [Text(72, 300, "2. Numerals:"), Text(90, 320, "(i) one;"), Text(90, 338, "(ii) two;")]
    lines += [
        Text(90, 356, "(iii) three;"),
        Text(90, 374, "(iv) four;"),
        Text(90, 392, "(v) five."),
    ]
    result = by_id(body(*lines))

    assert "1(i)" in result and result["1(i)"].normalized_text.startswith("(i) item after h")
    assert [k for k in result if k.startswith("2(")] == ["2(i)", "2(ii)", "2(iii)", "2(iv)", "2(v)"]


def test_unexpected_list_marker_is_not_turned_into_a_clause() -> None:
    result = by_id(
        body(
            Text(72, 100, "1. Owned fund means the aggregate of (i) paid up capital,"),
            Text(72, 120, "(vi) of any other reserve, as referred to elsewhere."),
        )
    )
    assert numbered(result) == ["1"]
    assert "unexpected_list_marker:(vi)" in result["1"].flags


def test_wrapped_chapter_heading_is_joined() -> None:
    content = build_pdf(
        [
            [
                Text(
                    72, 70, "Chapter-IV Directions applicable to entities in the Middle", bold=True
                ),
                centered(88, "Layer (ML)"),
                Text(72, 115, "A. Board", bold=True),
                Text(72, 140, "1. Text."),
            ]
        ]
    )
    doc = normalize_document(extract_pdf(content))
    assert [e.text for e in doc.outline] == [
        "Chapter-IV Directions applicable to entities in the Middle Layer (ML)",
        "A. Board",
    ]


def test_document_without_structure_is_unstructured_and_warned() -> None:
    content = build_pdf([[Text(72, 100, "Some text without any headings or numbering at all.")]])
    doc = normalize_document(extract_pdf(content))
    assert {u.unit_type for u in doc.units} == {"unstructured_page_block"}
    assert any("No numbered paragraphs" in w for w in doc.warnings)


def test_units_on_pages_needing_review_are_flagged() -> None:
    content = build_pdf(
        [
            [
                centered(70, "Chapter I - General"),
                Text(72, 100, "1. A paragraph that is long enough."),
            ],
            [Text(72, 100, "end")],
        ]
    )
    doc = normalize_document(extract_pdf(content))
    tail = [u for u in doc.units if u.page_end == 2][0]
    assert "page_needs_manual_review" in tail.flags and tail.extraction_status == "needs_review"


# -- text helpers ----------------------------------------------------------------------
def test_normalize_text_unifies_variants_only() -> None:
    assert normalize_text("NBFC – Middle Layer  ‘x’ “y”") == ("NBFC - Middle Layer 'x' \"y\"")
    assert normalize_text("ﬁnance") == "finance"  # NFKC ligature


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["Non-", "Banking"], "Non-Banking"),
        (["NBFC-", "UL"], "NBFC-UL"),
        (["2025-", "26"], "2025- 26"),  # a hyphen after a digit is not a word break
        (["shall", "not"], "shall not"),
        (["a", "", "b"], "a b"),
    ],
)
def test_join_lines(lines: list[str], expected: str) -> None:
    assert join_lines(lines) == expected


def test_protected_tokens_detect_lost_negation_or_number() -> None:
    original = "shall not exceed 15 per cent"
    assert protected_tokens(original) == protected_tokens("shall  not exceed 15 per cent")
    assert protected_tokens(original) != protected_tokens("shall exceed 15 per cent")
    assert protected_tokens(original) != protected_tokens("shall not exceed 1 per cent")
    assert protected_tokens(original) == Counter(
        {"shall": 1, "not": 1, "15": 1, "per": 1, "cent": 1}
    )


@pytest.mark.parametrize(
    ("text", "value"), [("i", 1), ("iv", 4), ("ix", 9), ("xl", 40), ("iiii", None), ("a", None)]
)
def test_roman_value(text: str, value: int | None) -> None:
    assert roman_value(text) == value
