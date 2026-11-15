"""CLI: ``uv run python -m app.ingestion [--verify-only | --import-dir DIR] [--source KEY ...]``.

``--import-dir`` imports PDFs a person downloaded in a browser, after the same
live RBI page verification; the files in DIR are only read.

Exit code 0 only if every selected source succeeded; 1 if any source was
blocked or failed; 2 for a configuration/manifest error.
"""

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.ingestion.manifest import (
    DEFAULT_MANIFEST_PATH,
    PLAN_ALLOWED_HOSTS,
    ManifestError,
    load_manifest,
)
from app.ingestion.raw_store import RawStore
from app.ingestion.rbi_collector import RbiCollector
from app.ingestion.safe_http import SafeHttpClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion",
        description="Verify and download the seeded RBI sources (read-only HTTPS GET).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="check the RBI pages against the manifest without downloading PDFs",
    )
    mode.add_argument(
        "--import-dir",
        type=Path,
        metavar="DIR",
        help="verify the RBI pages live, then import the matching PDFs from DIR "
        "(downloaded manually in a browser) instead of downloading them",
    )
    parser.add_argument(
        "--source", action="append", metavar="KEY", help="limit to a source_key (repeatable)"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        manifest, manifest_sha = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.import_dir is not None and not args.import_dir.is_dir():
        print(f"ERROR: import folder not found: {args.import_dir}", file=sys.stderr)
        return 2

    raw_root = settings.data_dir / "raw"
    reports_dir = settings.data_dir / "processed" / "ingestion_runs"
    allowed = PLAN_ALLOWED_HOSTS & {h.lower() for h in manifest.rbi_hosts}

    with SafeHttpClient(
        allowed_hosts=allowed,
        connect_timeout=settings.http_connect_timeout_seconds,
        read_timeout=settings.http_read_timeout_seconds,
        max_retries=settings.http_max_retries,
        retry_backoff=settings.http_retry_backoff_seconds,
        max_redirects=settings.http_max_redirects,
        user_agent=settings.http_user_agent,
    ) as client:
        collector = RbiCollector(
            manifest=manifest,
            manifest_path=args.manifest,
            manifest_sha256=manifest_sha,
            client=client,
            store=RawStore(raw_root),
            reports_dir=reports_dir,
            max_page_bytes=settings.http_max_page_bytes,
            max_pdf_bytes=settings.http_max_pdf_bytes,
            request_delay_seconds=settings.http_request_delay_seconds,
        )
        try:
            report = collector.run(
                verify_only=args.verify_only, only=args.source, import_dir=args.import_dir
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    for r in report.results:
        sha = r.stored_pdf.sha256[:12] if r.stored_pdf else "-"
        print(f"{r.source_key:32} {r.status.value:22} {r.history_status.value:32} sha256={sha}")
        if r.local_import:
            print(f"    imported {r.local_import.file_name} ({r.local_import.acquisition_method})")
        for check in r.checks:
            if not check.matched:
                print(
                    f"    MISMATCH {check.field}: expected={check.expected!r} "
                    f"observed={check.observed!r}"
                )
        for warning in r.warnings:
            print(f"    warning: {warning}")
        for error in r.errors:
            print(f"    error: {error}")
    print(f"\nSummary: {report.counts()}")
    print(f"Run report: {reports_dir / (report.run_id + '.json')}")
    return 0 if report.all_successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
