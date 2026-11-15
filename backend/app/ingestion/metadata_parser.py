"""Parse metadata from RBI Master Direction HTML pages.

Page content is untrusted data: it is only parsed for a fixed set of fields and
never interpreted as instructions. Parsing fails loudly (PageParseError) when
the expected structure is missing or ambiguous rather than guessing.

Structure observed on 14 Nov 2026 (current and history pages alike):
  <table class="tablebg">
    <td class="tableheader"><a id="APDF_..." href="<main PDF>">...</a></td>
    <td class="tableheader"><b>Official title</b></td>
    <tr class="tablecontent2"> <p>RBI/... <br> DOR....</p>
                               <p align="right">November 28, 2025</p> ...
"""

import re
import unicodedata
from datetime import date, datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

UPDATED_AS_ON_RE = re.compile(
    r"\(\s*Updated\s+as\s+on\s+([A-Za-z]+\.?\s+\d{1,2},\s*\d{4})\s*\)", re.I
)
HISTORY_LABEL_RE = re.compile(r"Updated\s+as\s+on\s+([A-Za-z]+\.?\s+\d{1,2},\s*\d{4})", re.I)
NO_PREVIOUS_VERSION_RE = re.compile(r"No\s+Master\s+Directions?\s*-\s*Pr[ei]+vious\s+Version", re.I)
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")


class PageParseError(Exception):
    """The page does not have the expected, unambiguous structure."""


class ObservedPageMetadata(BaseModel):
    title: str
    pdf_url: str
    issue_date: date | None
    issue_date_text: str | None
    updated_as_on: date | None
    document_identifiers: list[str]
    annex_pdf_urls: list[str]
    warnings: list[str]


class HistoryEntry(BaseModel):
    label: str
    history_id: str
    url: str
    entry_date: date | None


class PreviousVersionsObservation(BaseModel):
    entries: list[HistoryEntry]
    no_previous_version_message: bool


def normalize_title(text: str) -> str:
    """Normalise a title for comparison only (Unicode, dashes, spacing, case)."""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    return re.sub(r"\s+", " ", text).strip().casefold()


def parse_rbi_date(text: str) -> date | None:
    """Parse 'November 28, 2025' or 'Nov 28, 2025'. Returns None if unparseable."""
    cleaned = re.sub(r"\s+", " ", text.replace(".", "")).strip()
    cleaned = re.sub(r",\s*", ", ", cleaned)
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _text(node: Tag) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def parse_direction_page(html: str, page_url: str) -> ObservedPageMetadata:
    soup = BeautifulSoup(html, "html.parser")
    warnings: list[str] = []

    anchors = soup.select('a[id^="APDF_"]')
    if not anchors:
        raise PageParseError("No main PDF link (a[id^='APDF_']) found on the page.")
    if len(anchors) > 1:
        hrefs = [a.get("href") for a in anchors]
        raise PageParseError(f"Ambiguous page: {len(anchors)} main PDF links found: {hrefs}")
    href = (anchors[0].get("href") or "").strip()
    if not href:
        raise PageParseError("Main PDF link has no href.")
    pdf_url = urljoin(page_url, href)

    container = anchors[0].find_parent("table", class_="tablebg")
    if container is None:
        raise PageParseError("Main PDF link is not inside the expected 'tablebg' table.")

    titles = [_text(b) for b in container.select("td.tableheader b") if _text(b)]
    if not titles:
        raise PageParseError("No official title (td.tableheader b) found.")
    title = titles[0]

    content = container.select_one("tr.tablecontent2")
    identifiers: list[str] = []
    issue_date_text: str | None = None
    issue_date: date | None = None
    if content is None:
        warnings.append("Content block (tr.tablecontent2) not found; identifiers/date unknown.")
    else:
        first_p = content.find("p")
        if first_p is not None:
            identifiers = [
                line.strip()
                for line in first_p.get_text("\n", strip=True).split("\n")
                if line.strip()
            ]
        for p in content.find_all("p", attrs={"align": "right"}):
            candidate = _text(p)
            parsed = parse_rbi_date(candidate)
            if parsed is not None:
                issue_date_text, issue_date = candidate, parsed
                break
        if not identifiers:
            warnings.append("No document identifiers found.")
        if issue_date is None:
            warnings.append("No parseable issue date found.")

    updated_as_on = None
    match = UPDATED_AS_ON_RE.search(title)
    if match:
        updated_as_on = parse_rbi_date(match.group(1))
        if updated_as_on is None:
            warnings.append(f"Unparseable 'Updated as on' date in title: {match.group(1)!r}")

    annexes: list[str] = []
    for a in container.find_all("a", href=True):
        url = urljoin(page_url, a["href"].strip())
        if url != pdf_url and urlsplit(url).path.lower().endswith(".pdf") and url not in annexes:
            annexes.append(url)

    return ObservedPageMetadata(
        title=title,
        pdf_url=pdf_url,
        issue_date=issue_date,
        issue_date_text=issue_date_text,
        updated_as_on=updated_as_on,
        document_identifiers=identifiers,
        annex_pdf_urls=annexes,
        warnings=warnings,
    )


def parse_previous_versions_page(html: str, page_url: str) -> PreviousVersionsObservation:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[HistoryEntry] = []
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"].strip())
        query = {k.lower(): v for k, v in parse_qs(urlsplit(url).query).items()}
        if "histid" not in query:
            continue
        label = _text(a)
        match = HISTORY_LABEL_RE.search(label)
        entries.append(
            HistoryEntry(
                label=label,
                history_id=query["histid"][0],
                url=url,
                entry_date=parse_rbi_date(match.group(1)) if match else None,
            )
        )
    no_message = bool(NO_PREVIOUS_VERSION_RE.search(soup.get_text(" ", strip=True)))
    if not entries and not no_message:
        raise PageParseError(
            "Previous-versions page shows neither history entries nor RBI's "
            "'no previous version' message; cannot confirm history."
        )
    return PreviousVersionsObservation(entries=entries, no_previous_version_message=no_message)
