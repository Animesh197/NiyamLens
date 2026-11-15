"""End-to-end collector behaviour against a fake RBI server (no real network)."""

import json
from pathlib import Path

import httpx
import pytest

from app.ingestion.manifest import PLAN_ALLOWED_HOSTS, load_manifest
from app.ingestion.raw_store import RawStore
from app.ingestion.rbi_collector import RbiCollector
from app.ingestion.results import HistoryStatus, IngestionStatus
from app.ingestion.safe_http import SafeHttpClient
from tests.fixtures.rbi_html import (
    BOT_CHALLENGE_HTML,
    FAKE_PDF,
    direction_page,
    previous_versions_page,
)

MANIFEST, MANIFEST_SHA = load_manifest()
SOURCES = {s.source_key: s for s in MANIFEST.sources}
HISTORY_ENTRIES = {
    "nbfc_concentration_risk_2025": (("67", "Updated as on Nov 28, 2025"),),
    "nbfc_governance_2025": (("147", "Updated as on Nov 28, 2025"),),
    "nbfc_compliance_function_2026": (),
}


class FakeRbi:
    """Serves manifest-consistent pages; individual responses can be overridden."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.overrides: dict[str, httpx.Response] = {}
        self.pdf_bytes: dict[str, bytes] = {}
        self.routes: dict[str, httpx.Response] = {}
        for source in MANIFEST.sources:
            self.set_page(source.source_key)
        for family in MANIFEST.families:
            self.routes[family.previous_versions_url] = self._html(
                previous_versions_page(HISTORY_ENTRIES[family.family_key])
            )

    @staticmethod
    def _html(text: str) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=text)

    def set_page(self, key: str, **changes: object) -> None:
        s = SOURCES[key]
        kwargs = {
            "title": s.expected_title,
            "pdf_url": s.expected_pdf_url,
            "issue_date": s.expected_issue_date.strftime("%B %d, %Y"),
            "identifiers": tuple(s.expected_document_identifiers),
        }
        kwargs.update(changes)
        self.routes[s.source_page_url] = self._html(direction_page(**kwargs))

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.overrides:
            return self.overrides[url]
        if url in self.routes:
            return self.routes[url]
        for s in MANIFEST.sources:
            if url == s.expected_pdf_url:
                body = self.pdf_bytes.get(s.source_key, FAKE_PDF + s.source_key.encode())
                return httpx.Response(
                    200, headers={"content-type": "application/pdf"}, content=body
                )
        return httpx.Response(404)


@pytest.fixture
def fake() -> FakeRbi:
    return FakeRbi()


def make_collector(fake: FakeRbi, tmp_path: Path) -> RbiCollector:
    client = SafeHttpClient(
        allowed_hosts=PLAN_ALLOWED_HOSTS,
        connect_timeout=1,
        read_timeout=1,
        max_retries=0,
        retry_backoff=0,
        max_redirects=3,
        user_agent="test",
        transport=httpx.MockTransport(fake.handler),
        sleep=lambda _s: None,
    )
    return RbiCollector(
        manifest=MANIFEST,
        manifest_path=Path("source_manifest.yaml"),
        manifest_sha256=MANIFEST_SHA,
        client=client,
        store=RawStore(tmp_path / "raw"),
        reports_dir=tmp_path / "reports",
        max_page_bytes=1_000_000,
        max_pdf_bytes=1_000_000,
    )


def by_key(report) -> dict:
    return {r.source_key: r for r in report.results}


def pdf_files(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "raw").glob("*/pdf/*.pdf"))


def test_happy_path_ingests_all_five_with_history_states(fake: FakeRbi, tmp_path: Path) -> None:
    report = make_collector(fake, tmp_path).run()
    results = by_key(report)

    assert report.all_successful
    assert {r.status for r in report.results} == {IngestionStatus.INGESTED}
    assert len(pdf_files(tmp_path)) == 5
    assert results["crm_2025_historical_20251128"].history_status == HistoryStatus.CONFIRMED
    assert results["gov_2025_historical_20251128"].history_status == HistoryStatus.CONFIRMED
    assert results["cf_2026_current"].history_status == HistoryStatus.HISTORICAL_VERSION_UNAVAILABLE
    assert results["cf_2026_current"].comparison_eligible is False

    stored = results["crm_2025_current"].stored_pdf
    meta = json.loads((tmp_path / "raw" / stored.metadata_path).read_text())
    assert meta["final_url"] == SOURCES["crm_2025_current"].expected_pdf_url
    assert meta["expected"]["expected_title"] == SOURCES["crm_2025_current"].expected_title
    assert meta["observed"]["updated_as_on"] == "2026-08-25"
    assert meta["manifest_sha256"] == MANIFEST_SHA
    assert len(list((tmp_path / "reports").glob("*.json"))) == 1


def test_rerun_with_identical_bytes_is_idempotent(fake: FakeRbi, tmp_path: Path) -> None:
    make_collector(fake, tmp_path).run()
    before = {p: p.read_bytes() for p in pdf_files(tmp_path)}

    report = make_collector(fake, tmp_path).run()

    assert {r.status for r in report.results} == {IngestionStatus.UNCHANGED}
    assert {p: p.read_bytes() for p in pdf_files(tmp_path)} == before


def test_changed_bytes_become_a_new_immutable_version(fake: FakeRbi, tmp_path: Path) -> None:
    first = by_key(make_collector(fake, tmp_path).run())["gov_2025_current"]
    fake.pdf_bytes["gov_2025_current"] = FAKE_PDF + b"% amended\n"

    second = by_key(make_collector(fake, tmp_path).run())["gov_2025_current"]

    assert second.status == IngestionStatus.INGESTED_NEW_VERSION
    assert second.stored_pdf.previous_sha256 == first.stored_pdf.sha256
    assert (tmp_path / "raw" / first.stored_pdf.raw_path).exists()
    assert len(pdf_files(tmp_path)) == 6


@pytest.mark.parametrize(
    ("key", "changes", "failed_fields"),
    [
        # This title has no "(Updated as on …)" part, so only the title check fails.
        ("cf_2026_current", {"title": "Some Other Directions, 2026"}, ["title"]),
        # RBI embeds the update date in the title, so a newer update fails both checks.
        (
            "crm_2025_current",
            {
                "title": "Reserve Bank of India (Non-Banking Financial Companies - Concentration "
                "Risk Management) Directions, 2025 (Updated as on December 01, 2026)"
            },
            ["title", "updated_as_on"],
        ),
        (
            "gov_2025_current",
            {"pdf_url": "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NEWER.PDF"},
            ["pdf_url"],
        ),
        ("cf_2026_current", {"issue_date": "August 01, 2026"}, ["issue_date"]),
        ("cf_2026_current", {"identifiers": ("RBI/DoS/2026-27/999",)}, ["document_identifiers"]),
    ],
)
def test_identity_mismatch_blocks_without_downloading(
    fake: FakeRbi, tmp_path: Path, key: str, changes: dict, failed_fields: list[str]
) -> None:
    fake.set_page(key, **changes)

    result = by_key(make_collector(fake, tmp_path).run())[key]

    assert result.status == IngestionStatus.BLOCKED_MISMATCH
    assert [c.field for c in result.checks if not c.matched] == failed_fields
    assert result.stored_pdf is None
    assert SOURCES[key].expected_pdf_url not in fake.requests
    assert not any("NEWER.PDF" in u for u in fake.requests)


def test_missing_history_entry_blocks_historical_source(fake: FakeRbi, tmp_path: Path) -> None:
    fake.routes[MANIFEST.family("nbfc_governance_2025").previous_versions_url] = fake._html(
        previous_versions_page((("999", "Updated as on Nov 28, 2025"),))
    )

    result = by_key(make_collector(fake, tmp_path).run())["gov_2025_historical_20251128"]

    assert result.status == IngestionStatus.BLOCKED_MISMATCH
    assert result.history_status == HistoryStatus.NOT_FOUND


def test_bot_challenge_fails_closed_and_keeps_evidence(fake: FakeRbi, tmp_path: Path) -> None:
    url = SOURCES["cf_2026_current"].expected_pdf_url
    fake.overrides[url] = httpx.Response(
        200, headers={"content-type": "text/html"}, content=BOT_CHALLENGE_HTML
    )

    report = make_collector(fake, tmp_path).run()
    result = by_key(report)["cf_2026_current"]

    assert result.status == IngestionStatus.FAILED
    assert "bot-protection challenge" in result.errors[0]
    assert result.stored_pdf is None
    assert not (tmp_path / "raw" / "cf_2026_current" / "pdf").exists()
    evidence = tmp_path / "raw" / result.pdf_fetch.evidence_path
    assert evidence.name.startswith("rejected-") and evidence.read_bytes() == BOT_CHALLENGE_HTML
    # Other sources are unaffected; the run as a whole is reported as not successful.
    assert by_key(report)["crm_2025_current"].status == IngestionStatus.INGESTED
    assert report.all_successful is False


def test_off_domain_pdf_redirect_fails_and_is_never_followed(fake: FakeRbi, tmp_path: Path) -> None:
    url = SOURCES["crm_2025_current"].expected_pdf_url
    fake.overrides[url] = httpx.Response(302, headers={"location": "https://evil.example/x.pdf"})

    result = by_key(make_collector(fake, tmp_path).run())["crm_2025_current"]

    assert result.status == IngestionStatus.FAILED
    assert "SourcePolicyError" in result.errors[0]
    assert not any("evil.example" in u for u in fake.requests)


def test_new_history_for_no_history_family_warns_but_keeps_comparison_off(
    fake: FakeRbi, tmp_path: Path
) -> None:
    fake.routes[MANIFEST.family("nbfc_compliance_function_2026").previous_versions_url] = (
        fake._html(previous_versions_page((("500", "Updated as on Oct 01, 2026"),)))
    )

    result = by_key(make_collector(fake, tmp_path).run())["cf_2026_current"]

    assert result.status == IngestionStatus.INGESTED
    assert result.history_status == HistoryStatus.PREVIOUS_VERSIONS_LISTED
    assert result.comparison_eligible is False
    assert any("ask the user" in w for w in result.warnings)


def test_verify_only_never_requests_pdfs(fake: FakeRbi, tmp_path: Path) -> None:
    report = make_collector(fake, tmp_path).run(verify_only=True)

    assert {r.status for r in report.results} == {IngestionStatus.VERIFIED_ONLY}
    assert not any(u.upper().endswith(".PDF") for u in fake.requests)
    assert pdf_files(tmp_path) == []


def test_source_filter_and_unknown_key(fake: FakeRbi, tmp_path: Path) -> None:
    collector = make_collector(fake, tmp_path)
    report = collector.run(only=["cf_2026_current"])
    assert [r.source_key for r in report.results] == ["cf_2026_current"]
    with pytest.raises(ValueError, match="Unknown source_key"):
        collector.run(only=["nope_key"])


def test_page_served_as_non_html_fails(fake: FakeRbi, tmp_path: Path) -> None:
    fake.overrides[SOURCES["crm_2025_current"].source_page_url] = httpx.Response(
        200, headers={"content-type": "application/pdf"}, content=FAKE_PDF
    )
    result = by_key(make_collector(fake, tmp_path).run())["crm_2025_current"]
    assert result.status == IngestionStatus.FAILED
    assert "Expected an HTML page" in result.errors[0]


# -- Verified local import (PDFs downloaded manually in a browser) -----------


def pdf_name(key: str) -> str:
    return SOURCES[key].expected_pdf_url.rsplit("/", 1)[1]


def import_folder(tmp_path: Path, keys=None, *, lower_ext: bool = True) -> Path:
    folder = tmp_path / "inbox"
    folder.mkdir(exist_ok=True)
    for key in keys or SOURCES:
        name = pdf_name(key)
        if lower_ext:
            name = name[:-4] + ".pdf"  # browsers often save X.PDF as X.pdf
        (folder / name).write_bytes(FAKE_PDF + key.encode() + b"\n%%EOF\n")
    return folder


def test_import_verifies_pages_then_stores_local_files(fake: FakeRbi, tmp_path: Path) -> None:
    folder = import_folder(tmp_path)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}

    report = make_collector(fake, tmp_path).run(import_dir=folder)
    results = by_key(report)

    assert report.all_successful and report.import_dir == str(folder)
    assert {r.status for r in report.results} == {IngestionStatus.INGESTED}
    assert len(pdf_files(tmp_path)) == 5
    # Pages and history were checked live; no PDF was requested over HTTP.
    assert all(c.matched for r in report.results for c in r.checks)
    assert not any(u.upper().endswith(".PDF") for u in fake.requests)
    assert results["cf_2026_current"].history_status == HistoryStatus.HISTORICAL_VERSION_UNAVAILABLE

    result = results["gov_2025_current"]
    assert result.pdf_fetch is None
    assert result.local_import.acquisition_method == "manual_browser_download"
    assert result.local_import.sha256 == result.stored_pdf.sha256
    meta = json.loads((tmp_path / "raw" / result.stored_pdf.metadata_path).read_text())
    assert meta["acquisition_method"] == "manual_browser_download"
    assert meta["final_url"] is None and meta["status_code"] is None
    assert meta["verified_pdf_url"] == SOURCES["gov_2025_current"].expected_pdf_url
    assert meta["local_file_name"] == pdf_name("gov_2025_current")[:-4] + ".pdf"
    # The original files are left exactly as they were.
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_import_rerun_is_idempotent(fake: FakeRbi, tmp_path: Path) -> None:
    folder = import_folder(tmp_path)
    make_collector(fake, tmp_path).run(import_dir=folder)
    report = make_collector(fake, tmp_path).run(import_dir=folder)
    assert {r.status for r in report.results} == {IngestionStatus.UNCHANGED}
    assert len(pdf_files(tmp_path)) == 5


def test_import_mismatch_blocks_before_reading_files(fake: FakeRbi, tmp_path: Path) -> None:
    folder = import_folder(tmp_path)
    fake.set_page("gov_2025_current", pdf_url="https://rbidocs.rbi.org.in/rdocs/x/NEWER.PDF")

    result = by_key(make_collector(fake, tmp_path).run(import_dir=folder))["gov_2025_current"]

    assert result.status == IngestionStatus.BLOCKED_MISMATCH
    assert result.local_import is None and result.stored_pdf is None


def test_import_missing_file_fails_only_that_source(fake: FakeRbi, tmp_path: Path) -> None:
    keys = [k for k in SOURCES if k != "cf_2026_current"]
    report = make_collector(fake, tmp_path).run(import_dir=import_folder(tmp_path, keys))
    results = by_key(report)

    assert results["cf_2026_current"].status == IngestionStatus.FAILED
    assert "No file named" in results["cf_2026_current"].errors[0]
    assert results["crm_2025_current"].status == IngestionStatus.INGESTED
    assert report.all_successful is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (BOT_CHALLENGE_HTML, "bot-protection challenge"),
        (b"<!DOCTYPE html><html><body>Error</body></html>", "%PDF- signature"),
        (b"", "Empty"),
        (FAKE_PDF[:20], "incomplete"),  # truncated download: no %%EOF marker
    ],
)
def test_import_rejects_files_that_are_not_complete_pdfs(
    fake: FakeRbi, tmp_path: Path, content: bytes, message: str
) -> None:
    folder = import_folder(tmp_path)
    (folder / (pdf_name("cf_2026_current")[:-4] + ".pdf")).write_bytes(content)

    result = by_key(make_collector(fake, tmp_path).run(import_dir=folder))["cf_2026_current"]

    assert result.status == IngestionStatus.FAILED
    assert message in result.errors[0]
    assert not (tmp_path / "raw" / "cf_2026_current" / "pdf").exists()


def test_import_rejects_symlinked_files(fake: FakeRbi, tmp_path: Path) -> None:
    folder = import_folder(tmp_path)
    target = folder / (pdf_name("gov_2025_current")[:-4] + ".pdf")
    real = tmp_path / "elsewhere.pdf"
    real.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(real)

    result = by_key(make_collector(fake, tmp_path).run(import_dir=folder))["gov_2025_current"]

    assert result.status == IngestionStatus.FAILED
    assert "not a regular file" in result.errors[0]


def test_import_over_size_limit_fails(fake: FakeRbi, tmp_path: Path) -> None:
    folder = import_folder(tmp_path)
    (folder / (pdf_name("cf_2026_current")[:-4] + ".pdf")).write_bytes(
        FAKE_PDF + b"x" * 1_000_001 + b"%%EOF\n"
    )
    result = by_key(make_collector(fake, tmp_path).run(import_dir=folder))["cf_2026_current"]
    assert result.status == IngestionStatus.FAILED
    assert "limit" in result.errors[0]


def test_import_cannot_be_combined_with_verify_only(fake: FakeRbi, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        make_collector(fake, tmp_path).run(verify_only=True, import_dir=tmp_path)
