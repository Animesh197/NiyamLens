import json
import os
import stat
from pathlib import Path

import pytest

from app.ingestion.pdf_downloader import sha256_hex
from app.ingestion.raw_store import RawStore, RawStoreIntegrityError
from tests.fixtures.rbi_html import FAKE_PDF

KEY = "crm_2025_current"
V2 = FAKE_PDF.replace(b"%%EOF", b"% v2\n%%EOF")


def meta(ts: str) -> dict:
    return {"retrieved_at": ts, "final_url": "https://rbidocs.rbi.org.in/x.PDF"}


def test_first_store_writes_read_only_bytes_and_metadata(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    outcome = store.store_pdf(KEY, FAKE_PDF, meta("2026-11-14T00:00:00+00:00"))

    pdf_path = tmp_path / outcome.relative_path
    assert outcome.status == "stored_new"
    assert outcome.sha256 == sha256_hex(FAKE_PDF)
    assert pdf_path.read_bytes() == FAKE_PDF
    assert not os.stat(pdf_path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    record = json.loads((tmp_path / outcome.metadata_relative_path).read_text())
    assert record["sha256"] == outcome.sha256
    assert record["byte_size"] == len(FAKE_PDF)
    assert record["previous_sha256"] is None


def test_identical_bytes_are_idempotent(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    first = store.store_pdf(KEY, FAKE_PDF, meta("2026-11-14T00:00:00+00:00"))
    files_before = sorted(p.name for p in (tmp_path / KEY / "pdf").iterdir())

    second = store.store_pdf(KEY, FAKE_PDF, meta("2026-11-15T00:00:00+00:00"))

    assert second.status == "already_present"
    assert second.sha256 == first.sha256
    assert second.matches_latest is True
    assert sorted(p.name for p in (tmp_path / KEY / "pdf").iterdir()) == files_before
    record = json.loads((tmp_path / first.metadata_relative_path).read_text())
    assert record["retrieved_at"] == "2026-11-14T00:00:00+00:00"  # not rewritten


def test_changed_bytes_create_new_version_and_keep_old(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    old = store.store_pdf(KEY, FAKE_PDF, meta("2026-11-14T00:00:00+00:00"))
    new = store.store_pdf(KEY, V2, meta("2026-11-15T00:00:00+00:00"))

    assert new.status == "stored_new_version"
    assert new.previous_sha256 == old.sha256
    assert (tmp_path / old.relative_path).read_bytes() == FAKE_PDF
    assert (tmp_path / new.relative_path).read_bytes() == V2
    assert [r["sha256"] for r in store.pdf_records(KEY)] == [old.sha256, new.sha256]


def test_revert_to_older_bytes_is_flagged(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    store.store_pdf(KEY, FAKE_PDF, meta("2026-11-14T00:00:00+00:00"))
    store.store_pdf(KEY, V2, meta("2026-11-15T00:00:00+00:00"))
    again = store.store_pdf(KEY, FAKE_PDF, meta("2026-11-16T00:00:00+00:00"))
    assert again.status == "already_present"
    assert again.matches_latest is False


def test_tampered_file_is_detected(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    outcome = store.store_pdf(KEY, FAKE_PDF, meta("2026-11-14T00:00:00+00:00"))
    path = tmp_path / outcome.relative_path
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    path.write_bytes(b"%PDF-tampered")

    with pytest.raises(RawStoreIntegrityError):
        store.store_pdf(KEY, FAKE_PDF, meta("2026-11-15T00:00:00+00:00"))


@pytest.mark.parametrize("bad_key", ["../escape", "a/b", "UPPER", "", "x"])
def test_unsafe_source_keys_are_rejected(tmp_path: Path, bad_key: str) -> None:
    with pytest.raises(ValueError, match="Unsafe source_key"):
        RawStore(tmp_path).store_pdf(bad_key, FAKE_PDF, meta("2026-11-14T00:00:00+00:00"))


def test_evidence_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    sha1, path1 = store.store_evidence(KEY, "page", b"<html>a</html>")
    sha2, path2 = store.store_evidence(KEY, "page", b"<html>a</html>")
    assert (sha1, path1) == (sha2, path2)
    assert path1 == f"{KEY}/evidence/page-{sha1}.html"
    assert len(list((tmp_path / KEY / "evidence").iterdir())) == 1
