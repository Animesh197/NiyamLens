import json
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.extract import extract_source, output_name
from app.ingestion.raw_store import RawStore
from tests.fixtures.pdf_builder import directions_pdf

KEY = "cf_2026_current"


def stored(tmp_path: Path) -> tuple[RawStore, Path]:
    store = RawStore(tmp_path / "raw")
    store.store_pdf(
        KEY,
        directions_pdf(),
        {
            "retrieved_at": datetime.now(UTC).isoformat(),
            "acquisition_method": "manual_browser_download",
            "verified_pdf_url": "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/X.PDF",
            "document_family_key": "nbfc_compliance_function_2026",
            "version_role": "current",
        },
    )
    return store, tmp_path / "extracted"


def test_extracts_writes_once_and_is_idempotent(tmp_path: Path) -> None:
    store, out = stored(tmp_path)

    status, summary = extract_source(store, out, KEY)
    assert status == "extracted"
    path = Path(summary["output"])
    assert path.name == output_name(summary["sha256"])
    payload = json.loads(path.read_text())
    assert payload["source"]["acquisition_method"] == "manual_browser_download"
    assert payload["extraction"]["sha256"] == payload["source"]["sha256"]
    assert [p["page_number"] for p in payload["extraction"]["pages"]] == [1, 2, 3]
    assert "lines" not in payload["extraction"]["pages"][0]
    assert any(u["section_identifier"] == "3(1)(ii)" for u in payload["sections"]["units"])
    first_bytes = path.read_bytes()

    status, _ = extract_source(store, out, KEY)
    assert status == "unchanged"
    assert path.read_bytes() == first_bytes


def test_existing_output_with_different_content_is_not_overwritten(tmp_path: Path) -> None:
    store, out = stored(tmp_path)
    _, summary = extract_source(store, out, KEY)
    path = Path(summary["output"])
    payload = json.loads(path.read_text())
    payload["sections"]["units"][0]["content_hash"] = "0" * 64
    path.write_text(json.dumps(payload))

    status, summary = extract_source(store, out, KEY)

    assert status == "failed"
    assert "refusing to overwrite" in summary["error"]
    assert json.loads(path.read_text())["sections"]["units"][0]["content_hash"] == "0" * 64


def test_missing_pdf_fails(tmp_path: Path) -> None:
    status, summary = extract_source(RawStore(tmp_path / "raw"), tmp_path / "out", KEY)
    assert status == "failed" and "No stored PDF" in summary["error"]


def test_tampered_raw_pdf_is_refused(tmp_path: Path) -> None:
    store, out = stored(tmp_path)
    raw = next((tmp_path / "raw" / KEY / "pdf").glob("*.pdf"))
    raw.chmod(0o644)
    raw.write_bytes(directions_pdf() + b"\n% tampered\n")

    status, summary = extract_source(store, out, KEY)

    assert status == "failed" and "refusing to extract" in summary["error"]
