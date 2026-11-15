import hashlib
from datetime import UTC, datetime

import pytest

from app.ingestion.pdf_downloader import PdfValidationError, sha256_hex, validate_pdf_response
from app.ingestion.safe_http import FetchResult
from tests.fixtures.rbi_html import BOT_CHALLENGE_HTML, FAKE_PDF


def fetch(content: bytes, *, status: int = 200, content_type: str | None = "application/pdf"):
    url = "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/X.PDF"
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=status,
        content_type=content_type,
        content=content,
        retrieved_at=datetime.now(UTC),
    )


def test_valid_pdf_passes() -> None:
    validate_pdf_response(fetch(FAKE_PDF))


def test_sha256_matches_hashlib() -> None:
    assert sha256_hex(FAKE_PDF) == hashlib.sha256(FAKE_PDF).hexdigest()


def test_html_error_page_is_rejected() -> None:
    page = b"<!DOCTYPE html><html><body>Error</body></html>"
    with pytest.raises(PdfValidationError, match="looks like an HTML page") as exc:
        validate_pdf_response(fetch(page, content_type="text/html"))
    assert exc.value.bot_challenge is False


def test_bot_challenge_page_is_identified_and_rejected() -> None:
    with pytest.raises(PdfValidationError, match="bot-protection challenge") as exc:
        validate_pdf_response(fetch(BOT_CHALLENGE_HTML, content_type="text/html"))
    assert exc.value.bot_challenge is True


def test_empty_body_is_rejected() -> None:
    with pytest.raises(PdfValidationError, match="Empty"):
        validate_pdf_response(fetch(b""))


def test_pdf_bytes_with_wrong_content_type_are_rejected() -> None:
    with pytest.raises(PdfValidationError, match="content type"):
        validate_pdf_response(fetch(FAKE_PDF, content_type="text/html"))


def test_non_200_is_rejected() -> None:
    with pytest.raises(PdfValidationError, match="HTTP 200"):
        validate_pdf_response(fetch(FAKE_PDF, status=206))
