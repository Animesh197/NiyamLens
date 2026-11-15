"""Download and validate RBI PDFs (implementationplan.md §2.4)."""

import hashlib
from collections.abc import Iterable

from app.ingestion.safe_http import FetchResult, SafeHttpClient

PDF_SIGNATURE = b"%PDF-"
ACCEPTED_PDF_MEDIA_TYPES = frozenset({"application/pdf", "application/x-pdf"})

# Text seen (14 Nov 2026) in the bot-protection page rbidocs.rbi.org.in returns
# to automated clients instead of the PDF. NiyamLens never tries to bypass it.
BOT_CHALLENGE_MARKERS = (
    b"testing whether you are a human visitor",
    b"please enable javascript to view the page content",
)


class PdfValidationError(Exception):
    """The response is not an acceptable PDF."""

    def __init__(self, message: str, *, bot_challenge: bool = False) -> None:
        super().__init__(message)
        self.bot_challenge = bot_challenge


def is_bot_challenge(content: bytes) -> bool:
    lowered = content[:200_000].lower()
    return any(marker in lowered for marker in BOT_CHALLENGE_MARKERS)


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_pdf_bytes(content: bytes, origin: str) -> None:
    """Check that ``content`` is non-empty and starts with the %PDF- signature.

    Shared by HTTP downloads and verified local imports. ``origin`` (a URL or a
    file name) is used only in error messages.
    """
    if not content:
        raise PdfValidationError(f"Empty response body from {origin}")
    head = content[:1024].lstrip()
    if not head.startswith(PDF_SIGNATURE) and is_bot_challenge(content):
        raise PdfValidationError(
            f"RBI returned a bot-protection challenge page instead of the PDF from "
            f"{origin}. Automated download is blocked; NiyamLens does not "
            "bypass access controls.",
            bot_challenge=True,
        )
    if not head.startswith(PDF_SIGNATURE):
        looks_html = head[:200].lower().startswith((b"<!doctype html", b"<html")) or (
            b"<html" in head.lower()
        )
        hint = " (response looks like an HTML page)" if looks_html else ""
        raise PdfValidationError(
            f"Response from {origin} does not start with the %PDF- signature{hint}"
        )


def validate_pdf_response(result: FetchResult) -> None:
    """Check status, content type, non-empty body and the %PDF- signature."""
    if result.status_code != 200:
        raise PdfValidationError(f"Expected HTTP 200 for PDF, got {result.status_code}")
    validate_pdf_bytes(result.content, result.final_url)
    if result.media_type not in ACCEPTED_PDF_MEDIA_TYPES:
        raise PdfValidationError(
            f"Unexpected content type {result.content_type!r} for PDF from {result.final_url}"
        )


def download_pdf(
    client: SafeHttpClient, url: str, *, max_bytes: int, allowed_hosts: Iterable[str]
) -> FetchResult:
    """GET a PDF over the safe client and validate it. Raises on any failure."""
    result = client.get(url, max_bytes=max_bytes, allowed_hosts=allowed_hosts)
    validate_pdf_response(result)
    return result
