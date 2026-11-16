"""CLI: ``uv run python -m app.ingestion.extract [--source KEY ...]``.

For each manifest source, takes the most recently stored raw PDF, checks its
SHA-256, extracts page text (pdf_extractor) and builds section units
(section_normalizer). Writes one JSON file per PDF version and tool version to
``data/processed/extracted/<source_key>/<sha256>.<tool-versions>.json``.

The output is write-once. A re-run with the same PDF and tool versions reports
``unchanged`` if the result is identical, and fails if it differs (the code
changed without a version bump). Database records are deferred to Milestone 4.

Exit code 0 only if every selected source was extracted; 1 if any failed;
2 for a configuration error.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.ingestion.manifest import DEFAULT_MANIFEST_PATH, ManifestError, load_manifest
from app.ingestion.pdf_extractor import PdfExtractionError, extract_pdf, extractor_info
from app.ingestion.raw_store import RawStore
from app.ingestion.section_normalizer import NORMALIZER_VERSION, normalize_document


def output_name(sha256: str) -> str:
    info = extractor_info()
    return f"{sha256}.pymupdf-{info.version}.normalizer-{NORMALIZER_VERSION}.json"


def _fingerprint(payload: dict[str, Any]) -> list[Any]:
    """The parts of a result that must be identical on a re-run (no timestamps)."""
    return [
        [(p["page_number"], p["char_count"], p["status"]) for p in payload["extraction"]["pages"]],
        [
            (u["section_identifier"], u["content_hash"], u["flags"])
            for u in payload["sections"]["units"]
        ],
    ]


def extract_source(store: RawStore, out_root: Path, source_key: str) -> tuple[str, dict[str, Any]]:
    """Returns (status, summary). status: extracted | unchanged | failed."""
    record = store.latest_pdf_record(source_key)
    if record is None:
        return "failed", {"error": "No stored PDF for this source; run ingestion first."}
    raw_path = store.root / record["raw_path"]
    try:
        document = extract_pdf(raw_path.read_bytes(), expected_sha256=record["sha256"])
    except (OSError, PdfExtractionError) as exc:
        return "failed", {"error": f"{type(exc).__name__}: {exc}"}
    sections = normalize_document(document)

    payload = {
        "source_key": source_key,
        "source": {
            "raw_path": record["raw_path"],
            "sha256": record["sha256"],
            "document_family_key": record.get("document_family_key"),
            "version_role": record.get("version_role"),
            "verified_pdf_url": record.get("verified_pdf_url") or record.get("final_url"),
            "acquisition_method": record.get("acquisition_method", "automated_download"),
        },
        # Line/span detail is used during normalization only; page text is kept.
        "extraction": json.loads(
            document.model_dump_json(exclude={"pages": {"__all__": {"lines"}}})
        ),
        "sections": json.loads(sections.model_dump_json()),
    }
    summary = {
        "sha256": document.sha256,
        "pages": document.page_count,
        "pages_needing_review": document.pages_needing_review,
        "units": sections.counts(),
        "warnings": document.warnings + sections.warnings,
    }

    out_dir = out_root / source_key
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / output_name(document.sha256)
    summary["output"] = str(path)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if _fingerprint(existing) == _fingerprint(payload):
            return "unchanged", summary
        summary["error"] = (
            f"{path.name} exists with different content. The extraction code changed without "
            "a version bump; refusing to overwrite."
        )
        return "failed", summary
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return "extracted", summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.extract",
        description="Extract page text and section units from the stored RBI PDFs.",
    )
    parser.add_argument(
        "--source", action="append", metavar="KEY", help="limit to a source_key (repeatable)"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        manifest, _ = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    keys = [s.source_key for s in manifest.sources]
    if args.source:
        unknown = sorted(set(args.source) - set(keys))
        if unknown:
            print(f"ERROR: Unknown source_key(s): {unknown}", file=sys.stderr)
            return 2
        keys = [k for k in keys if k in set(args.source)]

    store = RawStore(settings.data_dir / "raw")
    out_root = settings.data_dir / "processed" / "extracted"
    failed = 0
    for key in keys:
        status, summary = extract_source(store, out_root, key)
        failed += status == "failed"
        sha = summary.get("sha256", "-")[:12]
        print(f"{key:32} {status:10} sha256={sha} pages={summary.get('pages', '-')}")
        if "units" in summary:
            print(f"    units: {summary['units']}")
        if summary.get("pages_needing_review"):
            print(f"    pages needing manual review: {summary['pages_needing_review']}")
        for warning in summary.get("warnings", []):
            print(f"    warning: {warning}")
        if "error" in summary:
            print(f"    error: {summary['error']}")
        if "output" in summary and status != "failed":
            print(f"    output: {summary['output']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
