# NiyamLens

Regulatory change and compliance intelligence for Indian NBFCs: a human-reviewed research prototype.

> **Disclaimer.** NiyamLens is an information and decision-support prototype. It is **not legal advice** and **not an automated certification of regulatory compliance**. It does not replace a compliance officer and submits nothing to RBI or any other system. A qualified person must review every interpretation and approve every proposed action. Any NBFC profile or internal-control data used in this project is synthetic.

## Overview

NiyamLens will track a small, verified set of RBI NBFC directions. It will compare historical and current versions using deterministic diffs, extract candidate obligations with exact source citations, and assess likely applicability against a synthetic NBFC profile. It will then draft follow-up actions that a human reviewer approves or rejects. The full scope, corpus and milestone plan are in `implementationplan.md`. That file is the implementation contract and is kept out of Git.

**Current status: Milestone 1, project foundation.** No RBI ingestion, AI model, RAG, embeddings or agent features exist yet. The AI model and embedding provider are not yet chosen.

## Architecture (current foundation)

```text
frontend/  React + TypeScript + Vite + Tailwind CSS   static starter page (no API calls yet)
backend/   FastAPI (Python 3.11, uv)                  GET /api/v1/health (liveness only)
           ├── app/config.py            Pydantic Settings: environment variables + root .env
           └── app/database/session.py  SQLAlchemy 2 + Psycopg 3 engine, connection and pgvector checks
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

Locked direct dependencies (from `uv.lock`): fastapi 0.162.7, uvicorn[standard] 0.54.0, pydantic-settings 2.16.3 (pydantic 2.14.3, python-dotenv 1.2.4), sqlalchemy 2.1.8, psycopg[binary] 3.3.9. Dev tools: pytest 9.2.0, httpx 0.28.1, ruff 0.17.5.

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

Known warning: Starlette reports that using `httpx` with its test client is deprecated in favour of `httpx2`. The tests still pass. Switching packages is left for a future approved change.
