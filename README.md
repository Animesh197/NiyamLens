# NiyamLens

Regulatory change and compliance intelligence for Indian NBFCs: a human-reviewed research prototype.

> **Disclaimer.** NiyamLens is an information and decision-support prototype. It is **not legal advice** and **not an automated certification of regulatory compliance**. It does not replace a compliance officer and submits nothing to RBI or any other system. A qualified person must review every interpretation and approve every proposed action. Any NBFC profile or internal-control data used in this project is synthetic.

## Overview

NiyamLens will track a small, verified set of RBI NBFC directions. It will compare historical and current versions using deterministic diffs, extract candidate obligations with exact source citations, and assess likely applicability against a synthetic NBFC profile. It will then draft follow-up actions that a human reviewer approves or rejects. The full scope, corpus and milestone plan are in `implementationplan.md`. That file is the implementation contract and is kept out of Git.

**Current status: Milestone 3, PDF extraction and section normalization.** The five seeded RBI PDFs are stored (downloaded manually in a browser and imported with live verification, because RBI serves a bot-protection challenge to automated clients; see [RBI source ingestion](#rbi-source-ingestion)). Their text is extracted page by page and split into numbered clauses (see [PDF extraction and section normalization](#pdf-extraction-and-section-normalization)). No database records, version comparison, AI model, RAG, embeddings or agent features exist yet. The AI model and embedding provider are not yet chosen.

## Architecture (current state)

```text
frontend/  React + TypeScript + Vite + Tailwind CSS   static starter page (no API calls yet)
backend/   FastAPI (Python 3.11, uv)                  GET /api/v1/health (liveness only)
           ├── app/config.py            Pydantic Settings: environment variables + root .env
           ├── app/database/session.py  SQLAlchemy 2 + Psycopg 3 engine, connection and pgvector checks
           └── app/ingestion/           manifest-driven RBI collector (CLI: python -m app.ingestion)
               ├── source_manifest.yaml   5 seeded sources in 3 families (expected values only)
               ├── safe_http.py           allowlisted HTTPS GET, checked redirects, timeouts, retries, size cap
               ├── metadata_parser.py     reads title, dates, identifiers, PDF link and history entries
               ├── pdf_downloader.py      status / content-type / %PDF- checks, bot-challenge detection
               ├── raw_store.py           write-once, SHA-256-named raw files and evidence
               ├── rbi_collector.py       per-source verify → block on mismatch → download/import → store
               ├── pdf_extractor.py       PyMuPDF page text, one-based pages, empty-page review flags
               ├── section_normalizer.py  chapters, headings, numbered clauses, footnotes, unstructured blocks
               └── extract.py             CLI: python -m app.ingestion.extract
data/      raw/ and processed/ (git-ignored)    raw files, page evidence, run reports
docker-compose.yml                       PostgreSQL 16 + pgvector on 127.0.0.1:5433 (prepared, not yet run)
docker/postgres/init/                    enables the `vector` extension on first initialisation
```

The backend is a modular monolith. It does not connect to the database at import or startup, and the health endpoint never depends on PostgreSQL.

## Prerequisites

| Tool | Version used | Notes |
|---|---|---|
| Python | 3.11.15 | Pinned by `.python-version` (`3.11`) and `requires-python = ">=3.11,<3.12"`. |
| uv | 0.12.3 | Manages the Python environment and lockfile (`backend/uv.lock`). |
| Node.js / npm | 20.20.2 / 10.8.2 | Vite 8 requires Node `^20.19.0` or `>=22.12.0`. |
| Docker + Compose | not installed yet | Needed only for the local PostgreSQL + pgvector database. |

## Environment configuration

```bash
cp .env.example .env   # in the repository root; .env is git-ignored
```

Then replace the placeholder password in both `NIYAMLENS_DB_PASSWORD` and `DATABASE_URL`.

| Variable | Used by | Purpose |
|---|---|---|
| `NIYAMLENS_ENVIRONMENT` | backend | `development` (default), `test` or `production` |
| `NIYAMLENS_LOG_LEVEL` | backend | `INFO` by default |
| `DATABASE_URL` | backend | `postgresql+psycopg://USER:PASSWORD@127.0.0.1:5433/niyamlens`. No default; required only when database functions are called. |
| `NIYAMLENS_DB_USER`, `NIYAMLENS_DB_PASSWORD` | Docker Compose | Credentials for the isolated `niyamlens` database |
| `NIYAMLENS_TEST_DATABASE_URL` | integration test | Opt-in; see [Tests](#tests) |

Real environment variables override values in `.env`. The database URL is stored as a secret value, so it is masked when settings are printed. Database error messages show the URL with the password hidden.

## Backend

```bash
cd backend
uv sync                                                  # creates backend/.venv with Python 3.11
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/v1/health                 # {"status":"ok","service":"niyamlens-backend","version":"0.1.0"}
```

The interactive API docs are at `http://127.0.0.1:8000/docs`.

Locked direct dependencies (from `uv.lock`): fastapi 0.162.7, uvicorn[standard] 0.54.0, pydantic-settings 2.16.3 (pydantic 2.14.3, python-dotenv 1.2.4), sqlalchemy 2.1.8, psycopg[binary] 3.3.9, httpx 0.28.1, pyyaml 6.0.3, beautifulsoup4 4.15.0, pymupdf 1.28.2. Dev tools: pytest 9.2.0, ruff 0.17.5.

`pymupdf` is pinned to `>=1.28.2,<2`. PyMuPDF 2.x adds ten more packages (including `onnxruntime`, `numpy` and `networkx`) for layout analysis that this project does not use; 1.28.2 has no dependencies. PyMuPDF is licensed under the GNU AGPL 3.0 (or a commercial licence from Artifex), which is different from this repository's Apache 2.0 licence. Check that this suits how you plan to share or deploy the project.

## RBI source ingestion

The collector works only on the five sources in [`backend/app/ingestion/source_manifest.yaml`](backend/app/ingestion/source_manifest.yaml). Three families; only the first two are eligible for a historical/current comparison:

| Family | Sources | Comparison |
|---|---|---|
| NBFC Concentration Risk Management Directions, 2025 | 28 Nov 2025 history entry (Histid 67); current (updated 25 Aug 2026) | Eligible |
| NBFC Governance Directions, 2025 | 28 Nov 2025 history entry (Histid 147); current (updated 24 Jun 2026) | Eligible |
| NBFC Compliance Function Directions, 2026 | current only (issued 31 Jul 2026) | `historical_version_unavailable` |

```bash
cd backend
uv run python -m app.ingestion --verify-only     # check RBI pages against the manifest; no PDF download
uv run python -m app.ingestion                   # verify, then download, validate and store PDFs
uv run python -m app.ingestion --source cf_2026_current   # limit to one source (repeatable)
uv run python -m app.ingestion --import-dir ../rbi_pdf    # verify live, then import browser-downloaded PDFs
```

For each source the collector:

1. fetches the official RBI page (HTTPS GET only, to `www.rbi.org.in`, `rbi.org.in` or `rbidocs.rbi.org.in`; every redirect target is checked before it is followed) and keeps the page as evidence;
2. reads the title, issue date, "Updated as on" date, document identifiers and the main PDF link;
3. compares them with the manifest. For history pages it also confirms the entry on RBI's previous-versions page. For the Compliance Function family it records whether RBI still shows no previous version;
4. **blocks** the source (`blocked_mismatch`) and does not download anything if any value differs. It never substitutes a similar PDF;
5. otherwise downloads the PDF, checks HTTP 200, `application/pdf`, a non-empty body and the `%PDF-` signature, and stores the bytes unchanged.

Storage under `data/raw/<source_key>/` (git-ignored):

- `pdf/<sha256>.pdf` holds the raw bytes. It is read-only and never overwritten. Identical bytes on a re-run give `unchanged`; different bytes create a new file whose metadata records `previous_sha256`.
- `pdf/<sha256>.json` records the retrieval time (UTC), requested and final URL, redirects, status, content type, size, SHA-256, and the expected and observed metadata.
- `evidence/page-*.html`, `history-*.html` and `rejected-*.html` keep the RBI pages and any rejected download as evidence. RBI pages contain changing content, so each run usually adds a new page snapshot.

Each run also writes a report to `data/processed/ingestion_runs/<run_id>.json`. The command exits with code 0 only if every source succeeded.

Database records for ingested versions (plan §5.1 step 11) are deferred to Milestone 4. Until then, the JSON metadata above is the record.

### Known blocker: RBI bot protection on PDF downloads

Since 14 Nov 2026, `rbidocs.rbi.org.in` has returned an HTML bot-protection page ("This question is for testing whether you are a human visitor…") instead of the PDF to automated clients. The collector detects this, reports the source as `failed`, keeps the challenge page as `rejected-*.html` evidence, and stores no PDF. NiyamLens does **not** try to bypass this protection (no browser impersonation, cookie replay or CAPTCHA handling).

**Verified local import (the approved workaround).** A person downloads the five PDFs in a normal browser from the links in the manifest into `rbi_pdf/` (git-ignored). Then run `uv run python -m app.ingestion --import-dir ../rbi_pdf`. The import:

- still checks every RBI page and history entry live, and blocks on any mismatch;
- reads only the file whose name matches the *verified* PDF link (letter case ignored, because browsers may save `.PDF` as `.pdf`);
- rejects links/non-regular files, ambiguous matches, oversized files, empty files, HTML or bot-challenge pages, and files without the `%PDF-` signature or a `%%EOF` end marker (incomplete download);
- copies the bytes into the same write-once store and never modifies the original files.

The metadata records `acquisition_method: manual_browser_download`, the file name and modification time, and the import time as `retrieved_at`. NiyamLens did not see the browser's download, so `final_url`, `status_code` and `content_type` are `null`; the live page verification in the same run is what ties each file to its RBI source. Automated downloads record `acquisition_method: automated_download`.

## PDF extraction and section normalization

```bash
cd backend
uv run python -m app.ingestion.extract                      # all stored sources
uv run python -m app.ingestion.extract --source gov_2025_current
```

For each source the command takes the most recently stored raw PDF, checks its SHA-256 and the `%PDF-` signature, and then:

1. **Extracts every page with PyMuPDF.** Page numbers are one-based and are the PDF's physical page positions, not the numbers printed in the footer (those differ in these RBI PDFs). No OCR and no de-hyphenation are applied. A page with no text or fewer than 50 non-whitespace characters is marked `needs_manual_review`; no text is ever invented for it. The PyMuPDF/MuPDF versions and the extraction time are recorded.
2. **Splits the text into units:**
   - `paragraph` / `subparagraph`: numbered clauses such as `3`, `3(1)`, `3(1)(i)`, with chapter, section heading and start/end page. A number is accepted only if it continues the expected sequence. An out-of-sequence number or list marker stays as text and the unit is flagged, never guessed.
   - `footnote`: amendment footnotes at the bottom of a page (for example "Inserted vide … Amendment Directions, 2026"). The clause that cites a footnote lists its reference number in `footnote_markers`.
   - `unstructured_page_block`: cover and table of contents, introductions, signature blocks and annexes (reporting templates and forms). They never get a clause number and are marked for review.
3. **Keeps two texts per unit.** `original_text` holds the extracted lines unchanged. `normalized_text` is for comparison only: Unicode variants, dashes, quotes and spaces are unified, line breaks are joined (a hyphen at a line end is kept, as in `Non-Banking`), and small-font footnote reference numbers are removed. A check confirms that every number, negation ("not", "no", "unless", …) and modal verb ("shall", "may", …) is still present; a unit that fails it is flagged `normalization_check_failed`.

Printed page numbers and repeated running headers are left out of units; they remain in the stored page text.

Review flags include:
- `attachment_uncertain:unnumbered_proviso_or_note`: a "Provided that", "Explanation" or "Note" after a list item. It is kept with that item, but it may apply to the parent clause.
- `unexpected_list_marker:…` and `unexpected_paragraph_number:…`: a marker that doesn't continue the sequence. It is kept as text.
- `paragraph_number_gap:…`: a paragraph number skips one or more numbers.
- `page_needs_manual_review`: the unit sits on a page flagged during extraction.
- `no_reliable_paragraph_boundary`: set on every unstructured block.

Units marked `info:` are informational only.

Output (git-ignored, write-once): `data/processed/extracted/<source_key>/<sha256>.pymupdf-<version>.normalizer-<version>.json` with the source reference, page texts and warnings, the outline, and the units. A re-run with the same PDF and tool versions reports `unchanged`. If the result would differ (the code changed without a version bump), the command fails instead of overwriting. Database records are created in Milestone 4.

## Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run build      # type-check (tsc -b) and production build into frontend/dist
npm run lint       # oxlint (the linter shipped with the current Vite template)
```

Installed versions (from `package-lock.json`): react 19.3.1, react-dom 19.3.1, vite 8.3.8, @vitejs/plugin-react 6.3.0, typescript 7.0.2, tailwindcss 4.3.5, @tailwindcss/vite 4.3.5, oxlint 1.93.0.

## Database: PostgreSQL + pgvector (Docker Compose, pending)

`docker-compose.yml` is prepared but has **not been run or validated**, because Docker is not installed on the development machine. Once Docker is available:

```bash
cp .env.example .env            # set NIYAMLENS_DB_PASSWORD and DATABASE_URL first
docker compose up -d db         # image pgvector/pgvector:pg16
docker compose ps               # wait until the db service is "healthy"
```

- The container listens only on `127.0.0.1:5433`, separate from any other PostgreSQL server on port 5432. NiyamLens never uses port 5432.
- The database is `niyamlens`. The dedicated user comes from `NIYAMLENS_DB_USER`, and the password from `.env` only.
- Data persists in the named volume `niyamlens_pgdata`.
- `docker/postgres/init/01-enable-vector.sql` runs `CREATE EXTENSION IF NOT EXISTS vector;`. It runs **only when the volume is first created**. For an existing volume, run that statement manually.
- Known limitation: the image's initial user (`POSTGRES_USER`) is a database superuser. A least-privilege application role is planned for the schema milestone.

## Tests

```bash
cd backend
uv run pytest                    # unit tests; the integration test is skipped unless opted in
uv run ruff check .
uv run ruff format --check .
cd ../frontend && npm run build && npm run lint
```

**Opt-in database integration test.** `tests/integration/test_db_connection.py` runs only when `NIYAMLENS_TEST_DATABASE_URL` is set. It never falls back to `DATABASE_URL`. It fails before connecting unless the URL targets host `127.0.0.1`/`localhost`, port `5433` and database `niyamlens` or `niyamlens_*`. Its checks are read-only: the server version and the `vector` extension.

```bash
NIYAMLENS_TEST_DATABASE_URL='postgresql+psycopg://niyamlens:<password>@127.0.0.1:5433/niyamlens' \
  uv run pytest -m integration
```

### Milestone 1 results (14 Nov 2026)

| Check | Result |
|---|---|
| Backend unit tests (`uv run pytest`) | **21 passed, 1 skipped** (the opt-in integration test). One third-party deprecation warning, described below. |
| Ruff lint / format check | Passed / 9 files already formatted |
| Integration-test safety guard | Verified it refuses `:5432/backend_db`, `:5433/emi_sense_db` and `:5432/niyamlens` before any connection |
| Backend live start | `uvicorn` started; `GET /api/v1/health` returned HTTP 200 |
| Frontend `npm run build` (tsc + vite) | Passed |
| Frontend `npm run lint` (oxlint) | Passed (exit 0) |
| Frontend dev server | Started; page served with title "NiyamLens" |
| **PostgreSQL connection and pgvector check** | **Pending.** Not verified, because Docker is not installed and the isolated database has never run. |
| `docker-compose.yml` | Parsed as valid YAML only; not validated by Docker Compose |

### Milestone 2 results (14 Nov 2026)

| Check | Result |
|---|---|
| Backend unit tests (`uv run pytest`) | **97 passed, 1 skipped** (the opt-in database test). No test contacts RBI; they use fake HTTP responses. |
| Ruff lint / format check | Passed |
| Live `--verify-only` run against RBI | All 5 sources verified: title, issue date, "Updated as on" date, identifiers, PDF link and history entry all matched. Compliance Function confirmed `historical_version_unavailable`. |
| Live full run against RBI | **All 5 PDF downloads failed closed:** RBI returned a bot-protection page instead of each PDF. Evidence kept; no PDF stored; exit code 1. |
| Verified local import (15 Nov 2026) | Unit tests: **108 passed, 1 skipped**. Live `--import-dir ../rbi_pdf`: all 5 pages and history entries verified, **5 PDFs ingested** (`manual_browser_download`), exit code 0. Stored files are read-only and named by their SHA-256; the originals in `rbi_pdf/` were unchanged. |
| Re-run idempotency | Live: a second and third import reported **5 × `unchanged`**, exit code 0, still 5 stored PDFs. New-version handling is verified by unit tests only. |

### Milestone 3 results (15 Nov 2026)

| Check | Result |
|---|---|
| Backend unit tests (`uv run pytest`) | **150 passed, 1 skipped** (the opt-in database test). Extraction tests use synthetic PDFs generated at test time. |
| Ruff lint / format check | Passed |
| Live extraction of the 5 stored PDFs | All 5 extracted, exit code 0. 121 pages, none empty or low-text. |
| Paragraph numbering | Continuous from 1 with no gaps or duplicate identifiers: 53, 53, 47, 47 and 37 paragraphs. |
| Footnotes | 12 (Concentration Risk current) and 1 (Governance current) amendment footnotes; every footnote reference in a clause resolves to a footnote. None in the original 28 Nov 2025 versions. |
| Text coverage | Every extracted page line is in a unit or is a recognised heading, except the Compliance Function running header (page furniture). |
| Normalization check | No unit lost a number, negation or modal verb. |
| Re-run | All 5 `unchanged`; output files not rewritten. |

Units flagged for review on the real documents: 4–22 per document. They are unstructured blocks (cover, preamble, signature, annex pages) plus 6–11 unnumbered provisos or notes in each Concentration Risk and Governance version, and one inline list in paragraph 4(7) of the 28 Nov 2025 Concentration Risk version. One flag is a known false positive: Governance 5(9) has a line beginning "Explanation' to Section 178", which is a statutory reference, not a proviso.

Known warnings:
- Starlette reports that using `httpx` with its test client is deprecated in favour of `httpx2`. The tests still pass. Switching packages is left for a future approved change.
- Importing PyMuPDF prints Python `DeprecationWarning`s about SWIG types (`SwigPyPacked`, `SwigPyObject`, `swigvarlink`). They come from the library's bindings and do not affect results.
