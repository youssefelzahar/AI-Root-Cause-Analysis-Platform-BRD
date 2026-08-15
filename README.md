# AI Root Cause Analysis Platform

Implementation of the platform described in `plans/AI_Root_Cause_Analysis_Platform_PRD_v3.pdf`.

**Phase 1 — Data Foundation** is complete. A user uploads a CSV/Excel file or runs a SQL Server
query and saves its output; the dataset is automatically validated and profiled; the UI shows the
profile and quality status; the user configures a normalized KPI definition that marks the dataset
**Analysis Ready** for the future RCA engine.

```
Upload / Connect Dataset → Schema Validation → Data Profiling
    → KPI Detection → User KPI Selection → KPI Definition → RCA Engine
```

> ⚠️ **This deployment has no authentication.** Per the project decision, Phase 1 ships without
> login, JWT, or user password storage. Every dataset is attributed to a seeded default company
> and user, and the entire `/api` surface is open. **Do not expose it on a public network.**
> This is a deliberate deviation from PRD §21 ("an *authenticated* user"). Authentication attaches
> at a single seam — `get_current_context` in [deps.py](backend/app/api/deps.py) — without
> changing any route, service, or repository signature.

---

## 1. What was built

Everything below is new in this phase. The repository previously contained only a demo RCA
endpoint and one static dashboard page; there was no database layer, upload, storage, profiling,
validation, KPI detection, or SQL connector.

### 1.1 Data ingestion

| Feature | Detail |
|---|---|
| **CSV / TSV / TXT upload** | Streaming multipart upload, 200 MB cap |
| **Excel (.xlsx) upload** | 25 MB cap — see [§6 Known limitations](#6-known-limitations) |
| **Raw-body upload** | `POST /api/uploads/stream` avoids the multipart spool entirely |
| **Size enforcement** | ASGI middleware counting bytes off the wire, not a handler check |
| **Integrity** | SHA-256 checksum computed during the stream; optional duplicate rejection |
| **Atomic writes** | Written to `.tmp/*.part`, then `os.replace` — a failed upload never leaves a partial object |
| **UUID storage keys** | `{company_id}/{YYYY}/{MM}/{dataset_id}.{ext}` — the original filename never contributes a character to the path (PRD principle 2) |
| **Canonical form** | Every source materializes to Parquet via PyArrow, so one reader serves CSV, Excel, and SQL results |

### 1.2 Data profiling (PRD §9)

DuckDB, five passes, computed out-of-core so a 200 MB CSV never loads into Python memory.

- **Dataset level** — row count, column count, file size, duplicate row count and percentage,
  missing-cell count and percentage, engine, exact-vs-approximate quantile flag, duration.
- **Numeric columns** — min, max, mean, median, stddev, percentiles (p1/p5/p25/p50/p75/p95/p99),
  outlier count with the IQR fence bounds reported alongside.
- **Categorical columns** — distinct count, distinct percentage, null count/percentage, and
  top-K most frequent values with frequencies (gathered in a single `UNION ALL` scan).
- **Datetime columns** — min, max, detected frequency, distinct periods, missing dates.
- **Two-stage typed read** — stage A reads every column as `VARCHAR` so nothing can fail to parse;
  `TRY_CAST` counts then yield a per-column conversion confidence. Stage B re-reads with the
  resolved types. This is what makes "revenue detected as string, conversion confidence high,
  invalid values reported" a fact rather than a type sniffer's guess.

### 1.3 Schema validation (PRD §10) → `PASS` / `WARNING` / `BLOCKED`

Attempts conversion rather than rejecting on mismatch. Thresholds are named constants in
[validation_service.py](backend/app/services/validation_service.py), each pinned by a test:

| Constant | Value |
|---|---|
| `MIN_ROWS_ERROR` | 2 |
| `WEAK_SAMPLE_ROWS` | 30 |
| `COLUMN_HIGH_NULL_PCT` | 50 % |
| `DATASET_HIGH_MISSING_PCT` | 40 % |
| `DATASET_CRITICAL_MISSING_PCT` | 70 % |
| `HIGH_DUPLICATE_PCT` | 30 % |
| `ALL_DUPLICATE_PCT` | 99 % |
| `KPI_MEASURE_MAX_NULL_PCT` | 20 % |
| `MAX_DIMENSIONS` | 5 |

Two modes: `structural` (runs automatically after profiling) and `analysis` (re-runs against a
specific KPI definition). Every issue carries a code, severity, message, detected and suggested
type, conversion confidence, invalid-value count, and up to five invalid samples. Validation
history is retained and queryable.

### 1.4 KPI detection (PRD §11)

Heuristics over the profile, each candidate returned with a 0–1 score and human-readable reasons
so the UI can justify a "Recommended" badge — PRD principle 6 requires analytical claims to be
traceable.

- **Measures** — numeric or numeric-convertible, with a name boost for
  revenue/sales/amount/cost/profit/margin-style names. **Identifier exclusion runs first**:
  without it, `order_id` scores as a perfect measure.
- **Time columns** — native date/timestamp, or string with high date-parse confidence, plus a
  name-pattern bonus.
- **Dimensions** — bounded cardinality, short average string length (excludes free text), low
  null rate. Numeric columns are penalized ×0.6: they are usable as dimensions but are far more
  likely measures.
- **Suggested aggregation** — SUM for additive names, AVG for price/rate/ratio names.

Thresholds: `MEASURE_THRESHOLD 0.45`, `DIMENSION_THRESHOLD 0.40`, `TIME_THRESHOLD 0.50`.

### 1.5 KPI definition → Analysis Ready

Persists exactly the PRD §11 normalized shape — deliberately source-agnostic, so the RCA engine
never learns whether the data arrived as CSV, Excel, or SQL. The server re-validates that the
measure column is numeric-or-convertible, the time column is temporal-or-convertible, every
dimension exists and is dimension-eligible, and the aggregation is legal for the column's type.

**Aggregations:** `SUM` `AVG` `COUNT` `COUNT_DISTINCT` `MIN` `MAX` `MEDIAN`
**Comparison periods:** `previous_period` `previous_month` `previous_quarter` `previous_year` `custom`

### 1.6 SQL Server integration (PRD §8)

- **Saved connections** with the password **encrypted at rest** (Fernet, `v1:` versioned prefix,
  `MultiFernet` key rotation). The read schema has no password field at all, so a password cannot
  leak through the API even by mistake.
- **Read-only guard** parsed with `sqlglot`, not regex. Rejects anything that is not a single
  `SELECT`/`WITH`, then walks the AST for write nodes — including `SELECT … INTO`, which parses
  as a `Select` but creates a table, and `exp.Command`, which covers `EXEC`, `BACKUP`, `DBCC`,
  and `BULK INSERT`.
- **Defence in depth** — every query runs with `autocommit=False` inside a transaction that is
  **always rolled back** in a `finally` block. The guard being fooled is not sufficient to write.
- **Row caps via `cursor.fetchmany`**, never query rewriting: wrapping a user query in
  `SELECT TOP (n) …` produces invalid T-SQL when that query has its own `ORDER BY`.
- **Schema browser**, connection test that returns `200 {ok: false}` rather than an error status,
  a standalone lint endpoint, and **save-query-as-dataset**, which streams results into Parquet
  and then runs the identical validation + profiling pipeline as an upload.
- **Error sanitization** — driver errors routinely embed the whole connection string, so they are
  redacted before they reach a response or a log.

### 1.7 Frontend

Next.js 16 App Router, React 19, Server Components by default with `"use client"` islands only
where interaction demands it.

| Route | What it does |
|---|---|
| `/` | Redirects to `/datasets` |
| `/datasets` | List with size, rows, columns, quality pill, status |
| `/datasets/upload` | Drag-and-drop upload, real progress bar, 4-stage pipeline stepper |
| `/datasets/[id]` | Metadata, validation summary, links onward |
| `/datasets/[id]/profile` | **Overview / Columns / Quality / Statistics** tabs |
| `/datasets/[id]/kpi` | KPI Setup — recommendations first, with reasons |
| `/sql`, `/sql/new`, `/sql/[connectionId]` | Connections, editor, result grid, save-as-dataset |
| `/investigations` | The pre-existing RCA demo dashboard |

Upload progress uses **XMLHttpRequest** because `fetch()` cannot report it. Status polling checks
document visibility and stops at a 10-minute deadline. The profile tabs are client state synced to
`?tab=` via `history.replaceState`, so one fetch serves all four tabs and deep links still work.

---

## 2. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + SQLAlchemy 2 + Alembic | |
| Database | PostgreSQL (JSONB) | SQLite is used only by the test suite |
| Profiling | **DuckDB** | Profiles a 200 MB CSV out-of-core |
| Materialization | PyArrow → Parquet | Every source type converges on one typed format |
| SQL Server | **pymssql** + sqlglot | manylinux wheels, so the slim image needs no ODBC driver |
| Secrets | `cryptography` Fernet | SQL credentials encrypted at rest |
| Frontend | Next.js 16 + React 19 | Hand-written CSS, token-based, no UI framework |

## 3. Repository layout

```
backend/app/
  api/routes/     context, uploads, datasets, sql_connections, sql_editor, rca
  api/deps.py     get_db, get_current_context   ← the single auth seam
  core/           config, security, logging, exceptions, middleware
  db/models/      company, user, dataset, profile, validation, kpi, sql_connection, enums
  db/migrations/  0001 initial schema · 0002 seed default context
  schemas/        Pydantic request/response contracts
  services/       dataset, profiling, validation, kpi, sql, storage, jobs, materialize, rca_engine
  analysis/       duckdb_session, type_inference, profiler, kpi_heuristics   (pure, no DB)
  storage/        base (abstraction) + local
  connectors/     sqlserver, sql_guard
  rca/            anomaly_detection, dimension_analysis, contribution, ranking,
                  statistical_validation
frontend/src/
  app/            routes
  components/     ui primitives + layout
  features/       datasets (upload, profile, kpi), sql-editor, rca
  lib/api/        typed client: base-url, http, errors, datasets, sql, uploads, rca
  styles/         tokens, base, components
docker/nginx/
```

**Tables:** `companies` · `users` · `datasets` · `dataset_profiles` · `column_profiles` ·
`schema_validations` · `kpi_definitions` · `sql_connections`

**Dataset status:** `pending_upload → uploaded → validating → profiling → profiled →
analysis_ready`, plus terminal `upload_failed`, `profiling_failed`, `blocked`.

---

## 4. Running it

### Docker

```bash
cp .env.example .env          # set ENCRYPTION_KEY for anything but local dev
docker compose up --build
```

Frontend http://localhost:3000 · API docs http://localhost:8000/docs · Gateway http://localhost:8080

Optional SQL Server for local testing: `docker compose --profile mssql up -d mssql`.

### Locally

The packaged default `DATABASE_URL` points at `@db:5432`, which only resolves inside the Compose
network. Running outside Docker needs a `backend/.env` — pydantic-settings reads `.env` relative
to the working directory, and uvicorn/alembic run from `backend/`.

```bash
docker compose up -d db                             # or point at any Postgres

cd backend
python -m venv .venv && .venv/Scripts/activate      # `source .venv/bin/activate` on POSIX
python -m pip install -r requirements-dev.txt
# backend/.env: DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/rca
alembic upgrade head                                # required: 0002 seeds the company
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
API_INTERNAL_URL=http://localhost:8000 API_PROXY_TARGET=http://localhost:8000 npm run dev
```

> Migrations are **not** applied automatically. Without `alembic upgrade head`, `/api/context`
> fails because the default company row does not exist.

### Verify

```bash
cd backend  && pytest && ruff check app tests
cd frontend && npm run lint && npm run typecheck && npm run build
```

---

## 5. Key design decisions

**Uploads never trust the client.** The 200 MB cap is enforced by ASGI middleware counting bytes
as they arrive ([middleware.py](backend/app/core/middleware.py)), because Starlette parses the
entire multipart body *before* the endpoint function runs — an in-handler check would let a
201 MB body spool to disk first. `Content-Length` is not trusted either: nginx runs with
`proxy_request_buffering off` and sends chunked bodies with no such header.

**Integer inference requires an integrality check.** DuckDB happily casts `'950.50'` to `BIGINT`
by truncating, so a plain `TRY_CAST` would classify a decimal revenue column as integer and
silently drop the fraction. Every integer candidate must additionally satisfy
`TRY_CAST(x AS DOUBLE) = floor(TRY_CAST(x AS DOUBLE))`.

**Detected frequency is nullable on purpose.** "Number of periods" and "missing dates" presuppose
a known frequency. It is inferred from the modal gap between distinct dates, and below a 0.6
confidence floor all three are reported as `null` rather than fabricated.

**SQL is guarded by a parser, not a regex**, and the real guarantee is the always-rollback
transaction plus a documented `db_datareader` login. See [§1.6](#16-sql-server-integration-prd-8).

**Credentials never reach a log.** A logging filter redacts credential-shaped values from messages
and structured `extra` fields. Separately, `sqlalchemy.engine` is pinned to WARNING unless
`DB_ECHO` is explicitly set — that logger emits every statement *and its bound parameters* at
INFO, so inheriting the root level would dump profiled column values and customer names into the
log wholesale.

**The frontend never fabricates data.** The previous `lib/api.ts` wrapped everything in try/catch
and returned a hardcoded fallback, so a dead backend looked healthy. The replacement throws a
typed `ApiError` and every screen renders a real error state. `ApiError` records the method and
path only — **never the request body**, which can hold a SQL password.

**Base URLs resolve in the right place.** `NEXT_PUBLIC_*` is inlined at build time, so a browser
bundle would permanently carry `localhost:8000`. Instead the browser always calls same-origin
`/api` via `rewrites()`, and server components read `API_INTERNAL_URL` at runtime.

---

## 6. Known limitations

- **Excel is capped at 25 MB**, not 200 MB. xlsx is zip-compressed and cannot be streamed for
  parsing, so openpyxl must materialize the workbook. The PRD's 200 MB guarantee is written
  about CSV.
- **Profiling runs in-process** via FastAPI `BackgroundTasks`. A restart mid-profile strands a
  dataset; a startup task reconciles those to `profiling_failed` and
  `POST /api/datasets/{id}/profile/regenerate` is the recovery path.
  [services/jobs.py](backend/app/services/jobs.py) is the single seam to swap in Celery.
- **Local disk storage is single-node.** `StorageBackend.as_local_file` is the migration path to
  object storage; nothing calls `local_path()` directly.
- **`run_migrations` is dead config** — the setting exists and is documented in `.env.example`,
  but nothing reads it. Run Alembic explicitly.
- **RCA on your own dataset is Phase 2.** Configuring a KPI marks a dataset Analysis Ready and
  stores the normalized definition; `/investigations` still runs on demonstration data. The RCA
  tree/evidence visualization and investigation history (PRD §17) are explicitly future phase.

## 7. PRD gaps this implementation had to resolve

The PRD leaves four things undefined. These choices are worth reviewing:

1. **"dataset status"** (§9) is never enumerated. Split into two distinct concepts:
   `datasets.status` (pipeline state) and `quality_status` (the validation verdict).
2. **"outlier indicators"** (§9) is undefined. Implemented as counts outside the 1.5 × IQR fences,
   with the bounds reported alongside so the number is interpretable.
3. **"number of periods" / "missing dates"** (§9) presuppose a known frequency — hence the
   confidence floor and nullable result described above.
4. **"comparison period"** (§11) has no enumerated values. Defined as the five listed in
   [§1.5](#15-kpi-definition--analysis-ready).

## 8. Test suite

`backend/tests/` — unit tests for type inference, KPI heuristics, validation rules, the SQL guard,
credential encryption, log redaction, local storage, and the RCA engine; integration tests for the
upload flow, the KPI API, and the SQL API.

Fixtures run against SQLite; models use `JSON().with_variant(JSONB, "postgresql")` so the same
schema is testable without a server.

## 9. API surface

`GET /docs` has the full generated reference.

```
GET    /api/context                                   seeded company/user (no auth)

POST   /api/uploads                                   multipart upload (CSV/TSV/TXT/XLSX)
POST   /api/uploads/stream                            raw-body upload, avoids the multipart spool
GET    /api/uploads/{id}/status

GET    /api/datasets                                  list, company-scoped
GET    /api/datasets/{id}                             detail
PATCH  /api/datasets/{id}                             rename / describe
DELETE /api/datasets/{id}                             row + stored object
GET    /api/datasets/{id}/status                      small payload for polling
GET    /api/datasets/{id}/preview                     first N rows
GET    /api/datasets/{id}/profile                     always 200, with a `state` field
GET    /api/datasets/{id}/profile/columns
POST   /api/datasets/{id}/profile/regenerate
GET    /api/datasets/{id}/validation                  PASS / WARNING / BLOCKED
GET    /api/datasets/{id}/validation/history
GET    /api/datasets/{id}/kpi-candidates              measures / time / dimensions, with reasons
POST   /api/datasets/{id}/kpi-definitions             → Analysis Ready
GET    /api/datasets/{id}/kpi-definitions
GET    /api/datasets/{id}/kpi-definitions/active
DELETE /api/datasets/{id}/kpi-definitions/{kpi_id}

POST   /api/sql-connections                           passwords never returned
GET    /api/sql-connections
GET|PATCH|DELETE /api/sql-connections/{id}
POST   /api/sql-connections/test                      test before saving
POST   /api/sql-connections/{id}/test
GET    /api/sql-connections/{id}/schema

POST   /api/sql/validate                              lint a statement without running it
POST   /api/sql/connections/{id}/execute
POST   /api/sql/connections/{id}/save-as-dataset

POST   /api/investigations                            existing RCA demo
GET    /api/demo
```
