# AI Root Cause Analysis Platform

Implementation of the platform described in `plans/AI_Root_Cause_Analysis_Platform_PRD_v3.pdf`.

**Phase 1 — Data Foundation** and **Phase 2 — Root Cause Analysis Engine** are both complete. A
user uploads a CSV/Excel file or runs a SQL Server query and saves its output; the dataset is
validated and profiled; the user configures a normalized KPI definition that marks it **Analysis
Ready**; and the RCA engine then explains why that KPI changed, naming the segments behind the
movement with the evidence for each claim.

```
Upload / Connect Dataset → Schema Validation → Data Profiling
    → KPI Detection → User KPI Selection → KPI Definition
    → RCA Engine → Ranked Drivers + Hierarchical Tree
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

### 1.7 Root cause analysis (Phase 2)

Given an Analysis Ready dataset, answer *"why did this KPI change?"* — one DuckDB scan, roughly
eight statements, no persistence.

**Contribution is a share of the net change, sign preserved.** `contribution_i = Δ_i / Δ_total`, so
contributions sum to exactly 1 and a segment moving *against* the KPI comes out negative — which is
what makes it identifiable as an **offsetting factor** rather than a top driver. Values above 100 %
and below 0 % are correct rather than clamped: if one exceeds 100 %, something else offset it.

**When the net change nearly cancels, the denominator switches to gross movement.** Below
`|Δ_total| / Σ|Δ_j| = 0.05` a net-basis share would exceed 1000 % and has stopped being a share, so
the engine reports `Δ_i / Σ|Δ_j|` instead and says so via `attribution.basis`. Under that basis it is
the *magnitudes* that sum to 1, and `evidence.contribution_sum` reports it that way — so the
invariant reads 1.0 on every basis. The gross denominator is fixed at level 1 and threaded down, or
each depth would renormalise against its own siblings and a child could show 77 % under a 50 % parent.

| Aggregation | How contribution is computed |
|---|---|
| `SUM`, `COUNT` | Additive over disjoint groups: `Δ_i / Δ_total` |
| `AVG` | **Centred mix/rate decomposition** — `rate = w̄·Δm` (the group's own average moved) and `mix = (m̄ − Ā)·Δw` (its share of volume moved). Exact: the two sum to ΔA |
| `COUNT_DISTINCT` | Additive **only if verified** — `Σ per-group distinct − total distinct == 0` means the dimension partitions the key set. Otherwise unattributable, with the overlap reported |
| `MIN`, `MAX`, `MEDIAN` | **Unattributable.** No valid decomposition exists, so per-segment values and changes are reported with `contribution: null` rather than a fabricated number |

**Drivers are classified by direction, then Pareto.** Primary drivers are the shortest prefix of
same-direction segments reaching 80 % of the change; the rest above 5 % are secondary; anything
moving the other way is an offsetting factor. A fixed threshold alone would return *zero* drivers
when a change is spread over twenty segments at 5 % each — a false negative indistinguishable from
"nothing explains this".

**The tree descends by explanatory power, not by biggest segment.** `E = Σ|Δ_j − Δ_total·s_j| / |Δ_total|`
measures how far a dimension deviates from *"everything moved in proportion to its size"*. `E = 0`
means the dimension explains nothing, and if no dimension clears the floor the engine reports the
change as **broad-based** instead of inventing a driver. Depth 3, decaying branching, and every
unexpanded node records its own `stop_reason`.

**Contribution at depth is a share of the global change**, at every level — so "58 %" always means
58 % of the KPI movement the user asked about. A child holding 100 % of a parent that is itself 2 %
of the total would otherwise render as "100 %" and read as the headline cause. The local view is
carried separately as `share_of_parent_change`.

Every node also carries **`expected_change` and `excess_change`** — what the segment would have done
at its baseline share, and the surprise on top. Without those two, a large segment always looks like
the driver simply because it is large.

**High-cardinality dimensions truncate losslessly.** Two settings bound one breakdown query —
`RCA_MAX_VALUES_PER_DIMENSION` (the top-K display bound) and `RCA_MAX_SEGMENTS_SCANNED` (an operator
row cap) — and the engine applies the lower of the two. Everything past it is folded into a single
`(other)` bucket computed by subtraction from the level total, so contributions still sum to 1 and a
`DIMENSION_TRUNCATED` notice says it happened. That bucket is never ranked and never descended into:
it holds many segments' movement at once, so treating it as a candidate would put it above every real
driver.

### 1.8 Evidence + Investigation layer (Phase 3)

The analysis above is correct but, until now, unprovable: nothing was persisted, and the engine's
statement counter threw the SQL away, so *"7 queries in 141 ms"* could not be checked. This phase
makes an investigation a **persisted, evidence-backed resource**. Fully deterministic — no LLM.

**An investigation is a snapshot, not an instruction to recompute.** `POST /api/investigations`
returns 201 with a `Location`, and reading it back re-reads a row. `POST /api/rca/investigations`
stays exactly as it was: that one is *the analysis* — stateless, 200, creates nothing. This one is
*the investigation*. Both are kept deliberately.

**Every important finding becomes a structured claim.** Fourteen evidence types, each with the
provenance to check it: the dataset, the sanitised relation, the source columns, the analysis routine
and — where the number was measured rather than derived — the **verbatim statement that produced
it**. A derived record carries `query: null`. There is no third option: no representative query, no
reconstruction. The provenance check asserts byte-identity against the trace, which is what makes
*never fabricate SQL* mechanical rather than aspirational.

**The query trace never stores a bound parameter.** `QueryRecord` has no field for one, by
construction. Filters and drill predicates bind their values, so a parameter can be a customer name;
only the count is kept. This is the same reasoning that pins `sqlalchemy.engine` logging to WARNING.

**Two tolerances, because they answer different questions.**
`rca.constants.CONTRIBUTION_SUM_TOLERANCE` is the correctness invariant — *did we lose rows?* — and is
deliberately **not** tunable: a tunable one would let an operator configure a lost-rows bug into a
green tick. `INVESTIGATION_RECONCILIATION_TOLERANCE` is the *reporting* band for the verdict, and the
value actually applied is persisted on every row, so a raised tolerance shows up in the record rather
than only in the environment.

**Reconciliation is computed over the complete decomposition**, including the residual bucket and
every immaterial segment — never over the primary/secondary/offsetting lists, which are a selection
and are not expected to sum to anything. `NOT_APPLICABLE` is load-bearing: a MEDIAN cannot be
decomposed at all, and reporting that as a *failure* would mark every such KPI as broken.

**Evidence quality fails on a broken identity or missing provenance — never on thin data.** The
airline dataset has 99.98 % of its rows outside both compared windows; that earns a caveat and reads
as `VALIDATED`, because the analysis of the rows inside the windows is sound. Thin data surfaces
instead as `confidence: low` on the affected records and in the `coverage` evidence. Keeping those
two judgements apart is the whole point of the quality summary.

**Explainability is not a contribution, and the schema says so.** They are separate columns, separate
evidence types, and separate blocks in the UI with different bar shapes. A contribution is a share of
the change and cannot exceed 100 %; explainability measures deviation from proportional movement, so
segments moving in opposite directions add to it without adding to the net change. The airline
fixture reports 132 % and that is correct.

**Tree drift has three states, because two causes are legitimate.** A pure split carries a child that
cannot be scored by deviation-from-proportional, and a truncated level loses its remainder with no
residual bucket to hold it. `DRIFT_UNEXPLAINED` is the third case — the lost-rows bug the engine's
warning log always existed for, now surfaced as a verdict.

**Evidence ids are derived, not generated.** `uuid5` over `(investigation, type, key)`, which is what
lets the tree reference the evidence behind each node without inserting rows and reading them back —
keeping the whole builder in the pure layer. It also makes reproducibility a one-line assertion.

**`PARTIAL` is not a failure.** The decomposition succeeded and something optional did not: the
anomaly step was skipped, a dimension was excluded or truncated, or the tree drifted unexplained.
Every reason lands in `limitations`. `FAILED` means there is no result — and the row is still
persisted, because an investigation that vanishes on failure has no audit trail and makes `FAILED`
dead vocabulary.

**Anomaly detection runs as a bounded extra step** on the same connection (the two engines project
into differently named temp tables), filling the `anomaly` and `trend` evidence types. Its failure is
a limitation, never a 5xx.

### 1.9 Frontend


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
| `/investigations` | Picker: the datasets that are Analysis Ready |
| `/investigations/[datasetId]` | The investigation — KPI summary, waterfall, drivers, tree, evidence |

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
  api/routes/     context, uploads, datasets, sql_connections, sql_editor, rca, anomalies,
                  investigations
  api/deps.py     get_db, get_current_context   ← the single auth seam
  core/           config, security, logging, exceptions, middleware
  db/models/      company, user, dataset, profile, validation, kpi, sql_connection,
                  investigation, enums
  db/migrations/  0001 initial schema · 0002 seed default context · 0003 investigation layer
  schemas/        Pydantic request/response contracts
  services/       dataset, dataset_source, profiling, validation, kpi, sql, storage, jobs,
                  materialize, rca, anomaly, investigation
  analysis/       duckdb_session, trace, type_inference, profiler, kpi_heuristics (pure, no DB)
  analysis/rca/   constants, models, casting, period_analysis, dimension_analysis,
                  contribution, ranking, tree, engine                       (pure, no DB)
  analysis/anomaly/ constants, models, baseline, scoring, detectors, series,
                  engine                                                    (pure, no DB)
  analysis/investigation/ constants, models, evidence, validation, decisions,
                  audit, graph, engine                                      (pure, no DB)
  storage/        base (abstraction) + local
  connectors/     sqlserver, sql_guard
frontend/src/
  app/            routes
  components/     ui primitives + layout
  features/       datasets (upload, profile, kpi), sql-editor, rca, rca/evidence, anomaly
  lib/api/        typed client: base-url, http, errors, datasets, sql, uploads, rca,
                  anomaly, investigations
  styles/         tokens, base, components
docker/nginx/
docs/             database-erd.pdf
scripts/          generate_erd.py
```

### Entity relationship diagram

**[docs/database-erd.pdf](docs/database-erd.pdf)** — all 8 tables with every column and type, the
13 foreign keys with their cardinality and `ON DELETE` behaviour, plus a second page explaining
why the model is shaped the way it is (the two `status` concepts, the two denormalised columns,
why per-column statistics are rows rather than a JSON blob).

```bash
python scripts/generate_erd.py
```

The generator has no third-party dependencies — it emits vector PDF directly — and **verifies
itself against SQLAlchemy's live metadata before writing**, exiting non-zero if a column has been
added or removed without updating the diagram.

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
- **Investigations are stored as of Phase 3** — this limitation is resolved. It used to read
  *"each request recomputes from Parquet, so there is no history and no shareable snapshot"*.
  `POST /api/investigations` now persists one, and `/investigations/{datasetId}/{investigationId}` is
  a permalink that re-reads it. The old stateless `POST /api/rca/investigations` is unchanged and
  still recomputes; it is kept because it is a different thing, not a legacy path.
- **RBAC is inherited, not implemented.** Tenant isolation and dataset ownership are enforced
  rigorously — every read is scoped by `company_id` in the service layer, and cross-tenant access
  returns 404 rather than 403 so the API never confirms another tenant's row exists. But there are no
  roles, because there is no authentication: `get_current_context` remains the single documented seam.
  Inventing a role model here would be a second seam to unpick later.
- **The query trace records the real local path.** The stored statement is byte-identical to what ran,
  which is what makes provenance checkable — and for a local-disk backend that statement contains the
  server's absolute storage path. The investigation's own `source_relation` is the sanitised
  storage-key form, so the path appears only in *View queries*. Redacting the prefix would remove the
  disclosure at the cost of the verbatim guarantee; that trade is deliberately left open.
- **The engine reads more date formats than the profiler infers.** `casting.time_expression` falls
  back through `DATE_FORMATS`, but Phase 1's profiler types a column with a plain `TRY_CAST`, so a
  column of `15-Jun-2026` is inferred as text and KPI validation blocks it before RCA is reachable.
  Aligning the two is a Phase 1 change.
- **A partial newest period is skipped, not analysed.** When the data's own step size shows the
  latest bucket is still filling, the engine steps back one period and says so — otherwise a
  half-collected month reads as a collapse. With an unknown reporting frequency it cannot tell, and
  keeps the period.

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

**500 tests.** Unit tests for type inference, KPI heuristics, validation rules, the SQL guard,
credential encryption, log redaction and local storage; plus the RCA engine —
contribution maths (including a property test that AVG's rate + mix effects reconstruct ΔA for
random inputs), period resolution across all five comparison settings, driver classification, the
generated SQL, and the engine driven against real DuckDB relations. Integration tests cover the
upload flow, the KPI API, the SQL API and the RCA API.

The golden RCA fixture is the PRD's own worked example, and the test proves the engine *discovers*
`Cairo → A → Enterprise` from the arithmetic — the expected answer is nowhere in the engine. Two
tests guard the string-typed-Parquet trap: one compares a currency-formatted measure against a
numeric one, and one uploads a real **xlsx** so `excel_to_parquet` genuinely produces all-string
columns, then asserts identical totals.

The evidence layer adds the query trace (including a test that a filtered KPI's filter value appears
in no stored SQL and no record attribute), the evidence builder and validator, the decision trace,
and the investigation API end to end — lifecycle, tenant isolation on every endpoint, and the
acceptance case below.

The section 23 acceptance fixture is 43 rows engineered so the whole expected answer falls out of the
arithmetic: `Value For Money` 65 → 50, −15 at −23.1 %, **Singapore Airlines** as a GONE primary
driver at −12 / 80 %, five named secondaries, three offsetting factors, and the
`airline → sentiment: positive → cabin: Economy` hierarchy at −12 / 80 % on every level. Its
marginals are re-checked from the CSV in `test_investigation_fixture.py`, so a hand edit that breaks
the answer fails there rather than in the acceptance test where the cause would be hard to see. The
same fixture also produces an explainability of 132 %, which covers the *above 100 % is not an error*
case.

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

POST   /api/rca/investigations                        {dataset_id, kpi_definition_id?} -> 200
DELETE /api/rca/investigations/{dataset_id}           discards the KPI behind it -> 204

POST   /api/investigations                            {dataset_id, question?} -> 201 + Location
GET    /api/investigations                            ?dataset_id= &status= -> paged history
GET    /api/investigations/{id}                        findings, verdicts, decisions - no detail
GET    /api/investigations/{id}/evidence               ?type= repeatable -> paged records
GET    /api/investigations/{id}/tree                   the hierarchy, evidence ids per node
GET    /api/investigations/{id}/queries                every statement, verbatim
GET    /api/investigations/{id}/audit                  what happened, in order
GET    /api/evidence/{id}                              one record, addressable on its own

POST   /api/anomalies/detections                      {dataset_id, grain?, method?} -> 200
```

`POST /api/rca/investigations` returns 200 rather than 201: it is stateless and creates nothing.
`POST /api/investigations` returns **201** because it does — and 200 when it reused an equivalent
completed run over unchanged data, which is the honest reading of *reuse cached analytical results*:
the persisted row **is** the cache, and it is exact by construction. A result cache keyed on inputs
was deliberately not built, because a stale hit would attach a real query trace to numbers that trace
did not produce — precisely the fabrication the evidence layer exists to prevent.

`GET /api/investigations/{id}` deliberately carries no evidence list, query trace or audit trail. They
are the largest parts of the record and the least often wanted, so each has its own endpoint and the
UI fetches them on first open.

Failure codes that are really workflow states — `DATASET_NOT_ANALYSIS_READY`,
`KPI_DEFINITION_NOT_FOUND`, `KPI_TIME_COLUMN_REQUIRED`, `RCA_COLUMN_MISSING` (schema drift),
`RCA_NO_PREVIOUS_PERIOD` — are rendered inline by the UI with the next step, not as crashes. Outcomes
that are not errors at all arrive as `state` (`no_data`, `no_previous_period`, `no_change`,
`unattributable`) plus a `notices` array recording every judgement the engine had to make.

**Wording is deliberate:** *driver*, *contributor*, *contribution*, *offsetting factor* — never
*cause*. The engine measures which segments moved with the KPI, not why they moved.

`POST /api/anomalies/detections` answers the question that comes *before* an investigation: is this
KPI behaving unusually against its own history? It builds the KPI time series at the profiled
reporting grain, learns a trailing baseline (median + MAD over the previous 12 periods) and scores
each period with the modified z-score `0.6745 * (actual - median) / MAD`, so the score reads as
robust standard deviations from normal and `3.5` is the published Iglewicz & Hoaglin cutoff. Every
threshold lives in `app/analysis/anomaly/constants.py` with its justification, and the response
echoes the ones it used.

Three distinctions the engine refuses to blur: a period with **no rows** is `MISSING`, never zero;
a **boundary period still being collected** is `PARTIAL` and never scored, because a half-collected
month is indistinguishable from a collapse; and a period with **too little history behind it** is
`INSUFFICIENT_HISTORY`, which is not the same claim as *normal*. It reports what it cannot see
(`limitations`) alongside what it found, and warns when the model does not fit the series at all —
a KPI with a weekly cycle flags every weekend, and `HIGH_ANOMALY_RATE` says so rather than handing
back fifty Saturdays.
