"""Structured ingestion results and run reports."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.ingestion.metadata_parser import ObservedPageMetadata, PreviousVersionsObservation


class IngestionStatus(StrEnum):
    INGESTED = "ingested"  # first stored version for this source
    INGESTED_NEW_VERSION = "ingested_new_version"  # bytes differ from the latest stored
    UNCHANGED = "unchanged"  # identical bytes already stored (idempotent re-run)
    VERIFIED_ONLY = "verified_only"  # --verify-only: identity confirmed, PDF not fetched
    BLOCKED_MISMATCH = "blocked_mismatch"  # observed identity differs from the manifest
    FAILED = "failed"  # network, policy, parsing, validation or storage error


class HistoryStatus(StrEnum):
    CONFIRMED = "history_entry_confirmed"
    NOT_FOUND = "history_entry_not_found"
    HISTORICAL_VERSION_UNAVAILABLE = "historical_version_unavailable"
    PREVIOUS_VERSIONS_LISTED = "previous_versions_listed"
    NOT_CHECKED = "not_checked"


class IdentityCheck(BaseModel):
    field: str
    expected: Any
    observed: Any
    matched: bool


class FetchRecord(BaseModel):
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    byte_size: int
    sha256: str
    retrieved_at: datetime
    redirect_chain: list[str] = Field(default_factory=list)
    attempts: int
    evidence_path: str | None = None


class LocalImportRecord(BaseModel):
    """A PDF a person downloaded in a browser and NiyamLens imported from disk.

    NiyamLens did not observe that download, so no HTTP status, content type or
    final URL is recorded for it. The RBI page verification for the same run
    is what ties the file to the source.
    """

    acquisition_method: str = "manual_browser_download"
    file_name: str
    byte_size: int
    sha256: str
    file_modified_at: datetime
    imported_at: datetime
    verified_pdf_url: str


class StoredPdf(BaseModel):
    store_status: str
    sha256: str
    byte_size: int
    raw_path: str
    metadata_path: str
    previous_sha256: str | None
    matches_latest_stored: bool


class SourceIngestionResult(BaseModel):
    source_key: str
    document_family_key: str
    version_role: str
    comparison_eligible: bool
    status: IngestionStatus
    started_at: datetime
    finished_at: datetime | None = None
    page_fetch: FetchRecord | None = None
    observed: ObservedPageMetadata | None = None
    history_status: HistoryStatus = HistoryStatus.NOT_CHECKED
    history_fetch: FetchRecord | None = None
    history: PreviousVersionsObservation | None = None
    checks: list[IdentityCheck] = Field(default_factory=list)
    pdf_fetch: FetchRecord | None = None
    local_import: LocalImportRecord | None = None
    stored_pdf: StoredPdf | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class IngestionRunReport(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    manifest_path: str
    manifest_sha256: str
    verify_only: bool
    import_dir: str | None = None  # set when PDFs came from a local folder, not a download
    results: list[SourceIngestionResult] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for result in self.results:
            out[result.status.value] = out.get(result.status.value, 0) + 1
        return out

    @property
    def all_successful(self) -> bool:
        ok = {
            IngestionStatus.INGESTED,
            IngestionStatus.INGESTED_NEW_VERSION,
            IngestionStatus.UNCHANGED,
            IngestionStatus.VERIFIED_ONLY,
        }
        return bool(self.results) and all(r.status in ok for r in self.results)
