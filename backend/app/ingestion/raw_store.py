"""Immutable, content-addressed storage for raw source files.

Layout under the raw root (``data/raw``)::

    <source_key>/pdf/<sha256>.pdf        raw PDF bytes, unchanged
    <source_key>/pdf/<sha256>.json       metadata from the first retrieval
    <source_key>/evidence/<kind>-<sha256>.html   RBI pages / rejected responses

Files are never overwritten: they are written to a temporary file and then
hard-linked into place, which fails if the target already exists. Stored files
are made read-only. If a source yields different bytes, a new file is created
and the metadata records the previous SHA-256.
"""

import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.ingestion.pdf_downloader import sha256_hex

_SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9_]{2,79}$")
EvidenceKind = Literal["page", "history", "rejected"]


class RawStoreIntegrityError(Exception):
    """A stored file no longer matches its recorded SHA-256."""


@dataclass(frozen=True)
class StoreOutcome:
    status: Literal["stored_new", "stored_new_version", "already_present"]
    sha256: str
    byte_size: int
    relative_path: str
    metadata_relative_path: str
    previous_sha256: str | None
    matches_latest: bool


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    # -- helpers -----------------------------------------------------------
    def _source_dir(self, source_key: str) -> Path:
        if not _SAFE_KEY.match(source_key):
            raise ValueError(f"Unsafe source_key for a storage path: {source_key!r}")
        return self.root / source_key

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    @staticmethod
    def _write_immutable(path: Path, content: bytes) -> bool:
        """Create ``path`` with ``content`` unless it exists. Returns True if created."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return False
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "xb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, path)  # atomic; raises if path already exists
            except FileExistsError:
                return False
        finally:
            tmp.unlink(missing_ok=True)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return True

    @staticmethod
    def _verify(path: Path, expected_sha256: str) -> None:
        actual = sha256_hex(path.read_bytes())
        if actual != expected_sha256:
            raise RawStoreIntegrityError(
                f"{path} has SHA-256 {actual}, expected {expected_sha256}. "
                "The stored file was modified; refusing to continue."
            )

    # -- PDFs --------------------------------------------------------------
    def pdf_records(self, source_key: str) -> list[dict[str, Any]]:
        """All stored PDF metadata records for a source, oldest first."""
        pdf_dir = self._source_dir(source_key) / "pdf"
        if not pdf_dir.is_dir():
            return []
        records = [json.loads(p.read_text(encoding="utf-8")) for p in pdf_dir.glob("*.json")]
        return sorted(records, key=lambda r: (r["retrieved_at"], r["sha256"]))

    def latest_pdf_record(self, source_key: str) -> dict[str, Any] | None:
        records = self.pdf_records(source_key)
        return records[-1] if records else None

    def store_pdf(self, source_key: str, content: bytes, metadata: dict[str, Any]) -> StoreOutcome:
        """Store PDF bytes immutably. ``metadata`` must include ``retrieved_at``."""
        if "retrieved_at" not in metadata:
            raise ValueError("metadata must include retrieved_at")
        sha = sha256_hex(content)
        pdf_dir = self._source_dir(source_key) / "pdf"
        pdf_path = pdf_dir / f"{sha}.pdf"
        meta_path = pdf_dir / f"{sha}.json"
        latest = self.latest_pdf_record(source_key)
        latest_sha = latest["sha256"] if latest else None

        if pdf_path.exists():
            self._verify(pdf_path, sha)
            if not meta_path.exists():
                raise RawStoreIntegrityError(f"{pdf_path} exists without metadata {meta_path}")
            return StoreOutcome(
                status="already_present",
                sha256=sha,
                byte_size=len(content),
                relative_path=self._rel(pdf_path),
                metadata_relative_path=self._rel(meta_path),
                previous_sha256=None,
                matches_latest=latest_sha == sha,
            )

        previous = latest_sha
        record = {
            **metadata,
            "source_key": source_key,
            "sha256": sha,
            "byte_size": len(content),
            "raw_path": self._rel(pdf_path),
            "previous_sha256": previous,
        }
        self._write_immutable(pdf_path, content)
        self._verify(pdf_path, sha)
        self._write_immutable(
            meta_path, (json.dumps(record, indent=2, sort_keys=True, default=str) + "\n").encode()
        )
        return StoreOutcome(
            status="stored_new_version" if previous else "stored_new",
            sha256=sha,
            byte_size=len(content),
            relative_path=self._rel(pdf_path),
            metadata_relative_path=self._rel(meta_path),
            previous_sha256=previous,
            matches_latest=True,
        )

    # -- evidence ----------------------------------------------------------
    def store_evidence(
        self, source_key: str, kind: EvidenceKind, content: bytes, suffix: str = ".html"
    ) -> tuple[str, str]:
        """Store a page/response as immutable evidence. Returns (sha256, relative path)."""
        sha = sha256_hex(content)
        path = self._source_dir(source_key) / "evidence" / f"{kind}-{sha}{suffix}"
        if not self._write_immutable(path, content):
            self._verify(path, sha)
        return sha, self._rel(path)
