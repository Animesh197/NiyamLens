"""Manifest-driven RBI collector (implementationplan.md §5.1, steps 1–10 and 12).

Per source:
 1. fetch the official RBI page (allowlisted GET) and keep it as evidence;
 2. parse title, dates, identifiers and the linked PDF;
 3. compare observed identity with the manifest; for history pages, confirm the
    entry on RBI's previous-versions page; for the no-history family, record
    whether RBI still shows no previous version;
 4. on any mismatch: stop (blocked_mismatch) and do not download the PDF;
 5. otherwise download the verified PDF, validate it, and store it immutably.

Verified local import (``import_dir``): RBI serves a bot-protection challenge
instead of PDFs to automated clients, and NiyamLens does not bypass it. A person
may download the PDFs in a browser into one folder. Steps 1–4 still run live;
only step 5 changes: the file whose name matches the *verified* PDF link is read
from the folder, validated, and stored the same way, recorded as
``manual_browser_download``. The original files are only read, never changed.

Step 11 (database records) is deferred to Milestone 4; until then the metadata
JSON written next to each raw PDF and the run report are the records.
"""

import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from app.ingestion.manifest import FamilyRecord, SourceManifest, SourceRecord
from app.ingestion.metadata_parser import (
    PageParseError,
    PreviousVersionsObservation,
    normalize_title,
    parse_direction_page,
    parse_previous_versions_page,
)
from app.ingestion.pdf_downloader import (
    PDF_SIGNATURE,
    PdfValidationError,
    sha256_hex,
    validate_pdf_bytes,
    validate_pdf_response,
)
from app.ingestion.raw_store import RawStore, RawStoreIntegrityError
from app.ingestion.results import (
    FetchRecord,
    HistoryStatus,
    IdentityCheck,
    IngestionRunReport,
    IngestionStatus,
    LocalImportRecord,
    SourceIngestionResult,
    StoredPdf,
)
from app.ingestion.safe_http import FetchError, FetchResult, SafeHttpClient, SourcePolicyError

PDF_EOF_MARKER = b"%%EOF"


class LocalImportError(Exception):
    """A locally downloaded PDF is missing, ambiguous, unsafe to read, or incomplete."""


EXPECTED_FAILURES = (
    SourcePolicyError,
    FetchError,
    PageParseError,
    PdfValidationError,
    RawStoreIntegrityError,
    LocalImportError,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _fetch_record(result: FetchResult, evidence_path: str | None = None) -> FetchRecord:
    return FetchRecord(
        requested_url=result.requested_url,
        final_url=result.final_url,
        status_code=result.status_code,
        content_type=result.content_type,
        byte_size=len(result.content),
        sha256=sha256_hex(result.content),
        retrieved_at=result.retrieved_at,
        redirect_chain=list(result.redirect_chain),
        attempts=result.attempts,
        evidence_path=evidence_path,
    )


def _url_key(url: str) -> tuple[str, str, str, str]:
    """Compare URLs case-insensitively on scheme/host only; path and query exactly."""
    parts = urlsplit(url)
    return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.path, parts.query)


def _decode_html(fetch: FetchResult) -> tuple[str, list[str]]:
    if fetch.media_type != "text/html":
        raise PageParseError(
            f"Expected an HTML page from {fetch.final_url}, got content type {fetch.content_type!r}"
        )
    try:
        return fetch.content.decode("utf-8"), []
    except UnicodeDecodeError:
        return fetch.content.decode("utf-8", errors="replace"), [
            f"Page {fetch.final_url} was not valid UTF-8; undecodable bytes were replaced."
        ]


class RbiCollector:
    def __init__(
        self,
        *,
        manifest: SourceManifest,
        manifest_path: Path,
        manifest_sha256: str,
        client: SafeHttpClient,
        store: RawStore,
        reports_dir: Path,
        max_page_bytes: int,
        max_pdf_bytes: int,
        request_delay_seconds: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.manifest = manifest
        self.manifest_path = manifest_path
        self.manifest_sha256 = manifest_sha256
        self.client = client
        self.store = store
        self.reports_dir = reports_dir
        self.max_page_bytes = max_page_bytes
        self.max_pdf_bytes = max_pdf_bytes
        self.request_delay_seconds = request_delay_seconds
        self._sleep = sleep
        self._history_cache: dict[str, tuple[FetchResult, PreviousVersionsObservation]] = {}

    # ---------------------------------------------------------------------
    def run(
        self,
        *,
        verify_only: bool = False,
        only: list[str] | None = None,
        import_dir: Path | None = None,
    ) -> IngestionRunReport:
        if verify_only and import_dir is not None:
            raise ValueError("verify_only and import_dir cannot be combined")
        sources = self.manifest.sources
        if only:
            unknown = sorted(set(only) - {s.source_key for s in sources})
            if unknown:
                raise ValueError(f"Unknown source_key(s): {unknown}")
            sources = [s for s in sources if s.source_key in set(only)]

        started = _now()
        report = IngestionRunReport(
            run_id=f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
            started_at=started,
            manifest_path=str(self.manifest_path),
            manifest_sha256=self.manifest_sha256,
            verify_only=verify_only,
            import_dir=str(import_dir) if import_dir is not None else None,
        )
        for index, source in enumerate(sources):
            if index and self.request_delay_seconds:
                self._sleep(self.request_delay_seconds)
            family = self.manifest.family(source.document_family_key)
            report.results.append(
                self.collect_source(source, family, verify_only=verify_only, import_dir=import_dir)
            )
        report.finished_at = _now()
        self._write_report(report)
        return report

    def _write_report(self, report: IngestionRunReport) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.reports_dir / f"{report.run_id}.json"
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(report.model_dump_json(indent=2) + "\n")
        return path

    # ---------------------------------------------------------------------
    def collect_source(
        self,
        source: SourceRecord,
        family: FamilyRecord,
        *,
        verify_only: bool = False,
        import_dir: Path | None = None,
    ) -> SourceIngestionResult:
        result = SourceIngestionResult(
            source_key=source.source_key,
            document_family_key=source.document_family_key,
            version_role=source.version_role,
            comparison_eligible=source.comparison_eligible,
            status=IngestionStatus.FAILED,
            started_at=_now(),
        )
        try:
            self._collect(source, family, result, verify_only=verify_only, import_dir=import_dir)
        except EXPECTED_FAILURES as exc:
            result.status = IngestionStatus.FAILED
            result.errors.append(f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one source must not abort the run
            result.status = IngestionStatus.FAILED
            result.errors.append(f"Unexpected {type(exc).__name__}: {exc}")
        result.finished_at = _now()
        return result

    def _collect(
        self,
        source: SourceRecord,
        family: FamilyRecord,
        result: SourceIngestionResult,
        *,
        verify_only: bool,
        import_dir: Path | None = None,
    ) -> None:
        hosts = source.allowed_hosts

        # 1–2. Official page, kept as evidence before parsing.
        page = self.client.get(
            source.source_page_url, max_bytes=self.max_page_bytes, allowed_hosts=hosts
        )
        _, page_evidence = self.store.store_evidence(source.source_key, "page", page.content)
        result.page_fetch = _fetch_record(page, page_evidence)
        html, decode_warnings = _decode_html(page)
        result.warnings.extend(decode_warnings)
        observed = parse_direction_page(html, page.final_url)
        result.observed = observed
        result.warnings.extend(observed.warnings)
        if observed.annex_pdf_urls:
            result.warnings.append(
                f"Page links {len(observed.annex_pdf_urls)} other PDF(s) (e.g. annexes) that are "
                "outside the seeded corpus and were not downloaded."
            )

        # 3. Identity checks against the manifest.
        def check(field: str, expected: object, observed_value: object, matched: bool) -> None:
            result.checks.append(
                IdentityCheck(
                    field=field, expected=expected, observed=observed_value, matched=matched
                )
            )

        check(
            "page_final_url",
            source.source_page_url,
            page.final_url,
            _url_key(page.final_url) == _url_key(source.source_page_url),
        )
        check(
            "title",
            source.expected_title,
            observed.title,
            normalize_title(observed.title) == normalize_title(source.expected_title),
        )
        check(
            "pdf_url",
            source.expected_pdf_url,
            observed.pdf_url,
            _url_key(observed.pdf_url) == _url_key(source.expected_pdf_url),
        )
        check(
            "issue_date",
            source.expected_issue_date,
            observed.issue_date,
            observed.issue_date == source.expected_issue_date,
        )
        check(
            "updated_as_on",
            source.expected_updated_as_on,
            observed.updated_as_on,
            observed.updated_as_on == source.expected_updated_as_on,
        )
        missing_ids = [
            i
            for i in source.expected_document_identifiers
            if i not in observed.document_identifiers
        ]
        check(
            "document_identifiers",
            source.expected_document_identifiers,
            observed.document_identifiers,
            not missing_ids,
        )

        self._check_history(source, family, result, check)

        if not all(c.matched for c in result.checks):
            failed = [c.field for c in result.checks if not c.matched]
            result.status = IngestionStatus.BLOCKED_MISMATCH
            result.errors.append(
                f"Observed identity does not match the manifest ({', '.join(failed)}). "
                "PDF not downloaded; review before changing the seeded scope."
            )
            return

        if verify_only:
            result.status = IngestionStatus.VERIFIED_ONLY
            return

        # 5. Obtain the verified PDF: download it, or read the verified local copy.
        if import_dir is not None:
            content, local = self._read_local_pdf(import_dir, observed.pdf_url)
            result.local_import = local
            acquisition: dict[str, object] = {
                "acquisition_method": local.acquisition_method,
                # Time NiyamLens imported the file; the browser download itself was
                # not observed, so no HTTP status, content type or final URL exists.
                "retrieved_at": local.imported_at.isoformat(),
                "requested_url": observed.pdf_url,
                "final_url": None,
                "redirect_chain": [],
                "status_code": None,
                "content_type": None,
                "local_file_name": local.file_name,
                "local_file_modified_at": local.file_modified_at.isoformat(),
            }
        else:
            if self.request_delay_seconds:
                self._sleep(self.request_delay_seconds)
            pdf = self.client.get(
                observed.pdf_url, max_bytes=self.max_pdf_bytes, allowed_hosts=hosts
            )
            try:
                validate_pdf_response(pdf)
            except PdfValidationError:
                is_pdf = pdf.content.lstrip().startswith(PDF_SIGNATURE)
                _, rejected = self.store.store_evidence(
                    source.source_key, "rejected", pdf.content, ".pdf" if is_pdf else ".html"
                )
                result.pdf_fetch = _fetch_record(pdf, rejected)
                raise
            result.pdf_fetch = _fetch_record(pdf)
            content = pdf.content
            acquisition = {
                "acquisition_method": "automated_download",
                "retrieved_at": pdf.retrieved_at.isoformat(),
                "requested_url": pdf.requested_url,
                "final_url": pdf.final_url,
                "redirect_chain": list(pdf.redirect_chain),
                "status_code": pdf.status_code,
                "content_type": pdf.content_type,
            }

        metadata = {
            **acquisition,
            "verified_pdf_url": observed.pdf_url,
            "document_family_key": source.document_family_key,
            "version_role": source.version_role,
            "comparison_eligible": source.comparison_eligible,
            "source_page_url": source.source_page_url,
            "source_page_final_url": page.final_url,
            "source_page_sha256": result.page_fetch.sha256,
            "source_page_evidence_path": page_evidence,
            "history_status": result.history_status.value,
            "manifest_sha256": self.manifest_sha256,
            "expected": json.loads(source.model_dump_json()),
            "observed": json.loads(observed.model_dump_json()),
        }
        outcome = self.store.store_pdf(source.source_key, content, metadata)
        result.stored_pdf = StoredPdf(
            store_status=outcome.status,
            sha256=outcome.sha256,
            byte_size=outcome.byte_size,
            raw_path=outcome.relative_path,
            metadata_path=outcome.metadata_relative_path,
            previous_sha256=outcome.previous_sha256,
            matches_latest_stored=outcome.matches_latest,
        )
        result.status = {
            "stored_new": IngestionStatus.INGESTED,
            "stored_new_version": IngestionStatus.INGESTED_NEW_VERSION,
            "already_present": IngestionStatus.UNCHANGED,
        }[outcome.status]
        if outcome.status == "already_present" and not outcome.matches_latest:
            result.warnings.append(
                "PDF bytes match an older stored version, not the most recent one."
            )

    def _read_local_pdf(self, import_dir: Path, pdf_url: str) -> tuple[bytes, LocalImportRecord]:
        """Read the file named like the verified PDF link from ``import_dir`` (read-only).

        The name must match the link's file name, ignoring letter case only
        (browsers may save ``X.PDF`` as ``X.pdf``). The file must be a regular
        file directly inside the folder, within the size limit, start with
        ``%PDF-`` and contain the ``%%EOF`` end marker (to catch partial downloads).
        """
        expected_name = unquote(PurePosixPath(urlsplit(pdf_url).path).name)
        if not expected_name.lower().endswith(".pdf"):
            raise LocalImportError(f"Verified PDF link has no .pdf file name: {pdf_url}")
        if not import_dir.is_dir():
            raise LocalImportError(f"Import folder not found: {import_dir}")
        matches = [p for p in import_dir.iterdir() if p.name.casefold() == expected_name.casefold()]
        if not matches:
            raise LocalImportError(
                f"No file named {expected_name} (any letter case) in {import_dir}. "
                f"Download it in a browser from {pdf_url}."
            )
        if len(matches) > 1:
            names = sorted(p.name for p in matches)
            raise LocalImportError(f"Ambiguous: several files match {expected_name}: {names}")
        path = matches[0]
        if path.is_symlink() or not path.is_file():
            raise LocalImportError(f"{path.name} is not a regular file (links are not followed)")
        stat_result = path.stat()
        if stat_result.st_size > self.max_pdf_bytes:
            raise LocalImportError(
                f"{path.name} is {stat_result.st_size} bytes, over the "
                f"{self.max_pdf_bytes}-byte limit"
            )
        content = path.read_bytes()
        if len(content) > self.max_pdf_bytes:
            raise LocalImportError(f"{path.name} grew past the size limit while being read")
        validate_pdf_bytes(content, path.name)
        if PDF_EOF_MARKER not in content[-2048:]:
            raise LocalImportError(
                f"{path.name} has no %%EOF marker near its end; the download may be incomplete"
            )
        return content, LocalImportRecord(
            file_name=path.name,
            byte_size=len(content),
            sha256=sha256_hex(content),
            file_modified_at=datetime.fromtimestamp(stat_result.st_mtime, UTC),
            imported_at=_now(),
            verified_pdf_url=pdf_url,
        )

    def _check_history(
        self,
        source: SourceRecord,
        family: FamilyRecord,
        result: SourceIngestionResult,
        check: Callable[[str, object, object, bool], None],
    ) -> None:
        cached = self._history_cache.get(family.previous_versions_url)
        if cached is None:
            fetch = self.client.get(
                family.previous_versions_url,
                max_bytes=self.max_page_bytes,
                allowed_hosts=source.allowed_hosts,
            )
            html, _ = _decode_html(fetch)
            cached = (fetch, parse_previous_versions_page(html, fetch.final_url))
            self._history_cache[family.previous_versions_url] = cached
        fetch, observation = cached
        _, evidence = self.store.store_evidence(source.source_key, "history", fetch.content)
        result.history_fetch = _fetch_record(fetch, evidence)
        result.history = observation

        if source.version_role == "historical":
            entry = next(
                (e for e in observation.entries if e.history_id == source.history_id), None
            )
            matched = entry is not None and entry.entry_date == source.expected_history_entry_date
            result.history_status = HistoryStatus.CONFIRMED if matched else HistoryStatus.NOT_FOUND
            check(
                "history_entry",
                {"history_id": source.history_id, "date": source.expected_history_entry_date},
                {"history_id": entry.history_id, "date": entry.entry_date} if entry else None,
                matched,
            )
        elif not family.comparison_eligible:
            if not observation.entries and observation.no_previous_version_message:
                result.history_status = HistoryStatus.HISTORICAL_VERSION_UNAVAILABLE
            else:
                result.history_status = HistoryStatus.PREVIOUS_VERSIONS_LISTED
                result.warnings.append(
                    f"RBI now lists {len(observation.entries)} previous version(s) for a family "
                    "the plan treats as having none. Comparison stays disabled; ask the user "
                    "before changing the seeded scope."
                )
        else:
            result.history_status = HistoryStatus.PREVIOUS_VERSIONS_LISTED
