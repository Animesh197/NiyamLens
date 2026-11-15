from pathlib import Path

import pytest
import yaml

from app.ingestion.manifest import DEFAULT_MANIFEST_PATH, ManifestError, load_manifest

EXPECTED_SOURCES = {
    "crm_2025_historical_20251128": ("nbfc_concentration_risk_2025", "historical", True),
    "crm_2025_current": ("nbfc_concentration_risk_2025", "current", True),
    "gov_2025_historical_20251128": ("nbfc_governance_2025", "historical", True),
    "gov_2025_current": ("nbfc_governance_2025", "current", True),
    "cf_2026_current": ("nbfc_compliance_function_2026", "current", False),
}


def test_seed_manifest_matches_plan_corpus() -> None:
    manifest, sha = load_manifest()
    assert len(sha) == 64
    assert len(manifest.families) == 3
    assert {
        s.source_key: (s.document_family_key, s.version_role, s.comparison_eligible)
        for s in manifest.sources
    } == EXPECTED_SOURCES
    assert manifest.family("nbfc_compliance_function_2026").comparison_eligible is False


def test_seed_manifest_history_ids_and_hosts() -> None:
    manifest, _ = load_manifest()
    by_key = {s.source_key: s for s in manifest.sources}
    assert by_key["crm_2025_historical_20251128"].history_id == "67"
    assert by_key["gov_2025_historical_20251128"].history_id == "147"
    for source in manifest.sources:
        assert set(source.allowed_hosts) == {"www.rbi.org.in", "rbi.org.in", "rbidocs.rbi.org.in"}


def _load_raw() -> dict:
    return yaml.safe_load(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["sources"].append(dict(d["sources"][0])), "duplicate source_key"),
        (lambda d: d["sources"][0].update(document_family_key="unknown_family"), "unknown"),
        (
            lambda d: d["sources"][0].update(expected_pdf_url="https://evil.example/x.pdf"),
            "allowlist",
        ),
        (
            lambda d: d["sources"][0].update(
                source_page_url="http://www.rbi.org.in/Scripts/x.aspx?Histid=1"
            ),
            "https",
        ),
        (
            lambda d: d["sources"][0].update(allowed_hosts=["www.rbi.org.in", "evil.example"]),
            "beyond the plan",
        ),
        (lambda d: d["sources"][0].update(expected_history_entry_date=None), "history"),
        (lambda d: d["sources"][4].update(comparison_eligible=True), "not comparison-eligible"),
        (lambda d: d["sources"][0].update(unexpected_field="x"), "Extra inputs"),
    ],
)
def test_invalid_manifests_are_rejected(tmp_path: Path, mutate, message: str) -> None:
    data = _load_raw()
    mutate(data)
    with pytest.raises(ManifestError, match=message):
        load_manifest(_write(tmp_path, data))


def test_missing_manifest_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="Cannot read"):
        load_manifest(tmp_path / "missing.yaml")
