# Evidence & validation infrastructure

How the evidence layer works, how it is built, and which rules it exists to enforce.

The RCA and anomaly engines answer *what moved and who moved it*. This layer answers the next
question — **why should anyone believe that** — by turning each finding into a structured claim
carrying the provenance to re-check it, then running a fixed validation pass over the whole set and
persisting the outcome as an addressable resource.

It is **fully deterministic**. No LLM, no scoring model, no sampling. Two runs over unchanged data
produce identical evidence ids, identical claims, identical stop reasons and an identical audit
trail.

---

## Contents

1. [What the layer is responsible for](#1-what-the-layer-is-responsible-for)
2. [Where the code lives](#2-where-the-code-lives)
3. [The build pipeline](#3-the-build-pipeline)
4. [Layer 0 — the trace](#4-layer-0--the-trace)
5. [Evidence records](#5-evidence-records)
6. [Validation](#6-validation)
7. [The decision trace](#7-the-decision-trace)
8. [The audit trail](#8-the-audit-trail)
9. [The tree as an evidence graph](#9-the-tree-as-an-evidence-graph)
10. [Outcome status](#10-outcome-status)
11. [Persistence](#11-persistence)
12. [API surface](#12-api-surface)
13. [Frontend](#13-frontend)
14. [Configuration](#14-configuration)
15. [Invariants — do not break these](#15-invariants--do-not-break-these)
16. [Tests](#16-tests)
17. [How to extend it](#17-how-to-extend-it)

---

## 1. What the layer is responsible for

| Responsibility | Output |
|---|---|
| **A claim per finding** | 14 evidence types, each with an id, a sentence, and the provenance to verify it |
| **Provenance** | Dataset, sanitised relation, source columns, analysis routine, and the **verbatim** statement that produced the number |
| **Reconciliation** | Does the complete decomposition account for the whole movement? Plus a three-state tree-drift verdict |
| **Evidence quality** | Six checks in a fixed order → `VALIDATED` / `WARNING` / `FAILED` / `NOT_APPLICABLE` |
| **A decision trace** | Why each period, basis, dimension and segment was chosen, and which threshold stopped each branch |
| **An audit trail** | What happened during the run, in order, on a reproducible clock |
| **An evidence graph** | The drill-down tree, with evidence ids and decision sequences on every node |
| **A persisted resource** | An investigation with an id, a status, a history and a permalink |

What it explicitly does **not** do: compute analysis. Every number comes from an engine that already
existed. If a value is not already on an engine result, it does not belong in evidence.

---

## 2. Where the code lives

The pure analysis package — no SQLAlchemy, no Pydantic, no storage, so the whole layer is testable
without a database or an HTTP client:

| Module | Lines | What it does |
|---|---|---|
| [engine.py](backend/app/analysis/investigation/engine.py) | 293 | Orchestrates the run and returns one `InvestigationOutcome` |
| [evidence.py](backend/app/analysis/investigation/evidence.py) | 939 | The Evidence Builder: one claim per finding, plus the claim wording helpers |
| [validation.py](backend/app/analysis/investigation/validation.py) | 682 | Reconciliation and the six quality checks |
| [decisions.py](backend/app/analysis/investigation/decisions.py) | 447 | The decision trace |
| [models.py](backend/app/analysis/investigation/models.py) | 230 | Frozen dataclasses, and the derived-id function |
| [audit.py](backend/app/analysis/investigation/audit.py) | 209 | The audit trail |
| [constants.py](backend/app/analysis/investigation/constants.py) | 160 | Thresholds, claim wording, stop-reason sentences, tool names |
| [graph.py](backend/app/analysis/investigation/graph.py) | 140 | The tree as an evidence-backed document |
| [trace.py](backend/app/analysis/trace.py) | 239 | `Probe`, `QueryTracer`, `DecisionRecord` — a peer of both engines, not part of either |

Everything that touches the database or storage:

| Module | What it does |
|---|---|
| [investigation_service.py](backend/app/services/investigation_service.py) | Resolves the dataset and KPI, builds the plan, persists the outcome, owns the lifecycle |
| [db/models/investigation.py](backend/app/db/models/investigation.py) | Four ORM tables |
| [db/models/enums.py](backend/app/db/models/enums.py) | Every vocabulary in this layer |
| [0003_investigation_layer.py](backend/app/db/migrations/versions/0003_investigation_layer.py) | The migration, hand-written so `JSONB` survives on PostgreSQL |
| [schemas/investigation.py](backend/app/schemas/investigation.py) | Wire contracts, mapped explicitly from the ORM in `from_row` |
| [routes/investigations.py](backend/app/api/routes/investigations.py) | Two routers: `/investigations` and `/evidence` |

Naming trap worth knowing: **`EvidenceRecord` in this layer is one structured claim.**
`app.analysis.rca.models.Evidence` and `app.analysis.anomaly.models.Evidence` are the flat
execution-counter objects that predate it. All three are deliberately left alone.

---

## 3. The build pipeline

The service assembles an `InvestigationPlan` — an `RcaSpec` plus identity and `Provenance` — and
hands it to [`investigate()`](backend/app/analysis/investigation/engine.py), which takes an open
DuckDB connection and returns everything one run produced.

```
InvestigationPlan (spec + provenance + tolerance)
        │
        ├─ 1. run_investigation()          RCA: periods, dimensions, contributions, tree
        │                                  ↳ records rca_statements = probe.queries.count
        ├─ 2. detect_anomalies()           bounded extra step; failure is a limitation, not a 500
        │
        ├─ 3. reconcile()                  contribution sum + three-state tree drift
        ├─ 4. evidence.build()             every record except the validation ones
        ├─ 5. validation.assess()          the six checks → one verdict
        ├─ 6. apply_verdict()              stamp validated / failed / n-a onto every record
        ├─ 7. build_validation_records()   one record per check, appended last
        │
        ├─ 8. decisions.record_all()       why each choice was made
        ├─ 9. graph.stamp_decisions()      attach evidence ids to each decision
        ├─10. graph.build()                the tree, with evidence ids per node
        │
        └─11. _status()                    COMPLETED or PARTIAL, with limitations
                    ↓
            InvestigationOutcome
```

**The order is load-bearing.** Reconciliation runs before the builder because the execution record
quotes its verdict. The builder runs before `assess()` because the checks report on the set it
produced. `build_validation_records()` runs last because a checklist that reported on itself would
be self-referential.

Steps 3–7 also explain why `rca_statements` is threaded through: the anomaly engine appends to the
*same* tracer, so `probe.queries.count` after step 2 is not what `RcaResult` reports. The provenance
check compares the RCA's own slice, not the total.

---

## 4. Layer 0 — the trace

[trace.py](backend/app/analysis/trace.py) is what makes *"7 queries in 141 ms"* inspectable. It
replaced two identical private `_Counter` classes that threw the SQL away.

**`QueryTracer`** records every statement an engine executes. Tracing is unconditional — two
`perf_counter` calls and one frozen dataclass against roughly eight statements per investigation.
An opt-in trace is the one that is off when you need it.

Each `QueryRecord` carries `sequence` (1-based execution order, and the trace's identity), a
`Purpose` from a fixed 10-value vocabulary, the **verbatim** `sql`, `parameter_count`,
`duration_ms`, `status`, `rows_returned`, and `depth` / `node_id` for the drill statements.

> **There is deliberately no `params` field.** Filters and drill predicates bind their values, so a
> parameter can be a customer name. Only the count is kept — the same reasoning that pins
> `sqlalchemy.engine` logging to `WARNING`.

`TracedCursor` proxies fetches and back-fills `rows_returned`, so every call site stays a one-keyword
diff and a caller that never fetches still gets a record. `find(purpose, node_id=...)` searches
*backwards*, because a drill level runs one statement per node and the builder wants the one that
expanded *this* node.

**`Probe`** is one optional out-parameter carrying both the tracer and the decision list. One object
rather than two keyword arguments on both engines — and a third the next time something needs
observing.

The 10 purposes, in the words the UI shows:

| Purpose | Shown as |
|---|---|
| `describe_relation` | Read the file's column types |
| `project_base_table` | Project the measure, time and dimensions once |
| `resolve_period_bounds` | Find the data's own date range |
| `kpi_period_totals` | Total the KPI for both periods |
| `dimension_breakdown` | Break every dimension down at once |
| `distinct_overlap_check` | Check whether distinct counts are additive |
| `drilldown_breakdown` | Break one segment down further |
| `series_base_table` | Project the series for anomaly detection |
| `series_bounds` | Find the series date range |
| `series_aggregate` | Aggregate the KPI per period |

A failed statement is recorded too, then re-raised — so a `FAILED` trace is diagnosable. The DuckDB
message is truncated to 500 characters, because an error can quote the offending literal back.

---

## 5. Evidence records

### 5.1 Derived ids

```python
EVIDENCE_NAMESPACE = uuid.UUID("6f9b1e2a-0000-5000-8000-000000000001")
evidence_id(investigation_id, evidence_type, key)  # uuid5 over f"{id}:{type}:{key}"
```

Ids are **content-derived, not generated**. That is what lets the evidence graph stamp evidence ids
onto tree nodes with no database in sight — with random ids the rows would have to be inserted, read
back and the tree patched afterwards, dragging the pure builder into the service layer. It also
makes reproducibility a one-line assertion.

`uuid5` is SHA-1 based. That is not a weakness here: this is a naming function, not a security
boundary, and the digest is part of the persisted contract, so it must not be "upgraded".

The same function (`derived_id`) also names the persisted query and audit rows.

### 5.2 Provenance

Stamped once per investigation and copied onto **every** record, because a record is meant to be
checkable on its own — including after being fetched by id with no investigation in hand.

- `dataset_id`, `dataset_name`
- `source_relation` — the **storage-key** form, never what `open_dataset_relation` yields. That one
  embeds an absolute server temp path which differs per request for xlsx, so storing it would leak
  the server layout and break reproducibility.
- `measure_column`, `time_column`, `filters`
- `physical_columns` — what `DESCRIBE` actually returned, so `source_columns` can be checked against
  the schema that was really read rather than the one that was expected.

### 5.3 The 14 evidence types

| Type | Tool | Key | Claims |
|---|---|---|---|
| `kpi_change` | `rca.period_analysis` | `root` | The headline movement. No contribution — the KPI *is* the whole movement, and 100% here would be circular |
| `comparison` | `rca.period_analysis` | `periods` | Which two windows, at what grain, anchored on the data's own latest timestamp; rows outside both; any excluded partial period |
| `dimension_change` | `rca.contribution` | `node_id` | How one segment moved. Deliberately carries **no** contribution — that is a separate record, so the two can never be read as one number |
| `contribution` | `rca.contribution` | `node_id` | What share of the movement a segment accounts for, with its role, rank and basis |
| `drill_down` | `rca.tree` | `node_id` | A node below depth 1, with its share of the total and of its parent |
| `drill_down` (stop) | `rca.tree` | `node_id#stop` | Why a branch ended. `derived=True`, so no query is claimed **and none is expected** |
| `new_segment` | `rca.contribution` | `node_id` | Rows appeared where there were none. Phrased on rows, with no percentage change |
| `gone_segment` | `rca.contribution` | `node_id` | Rows vanished entirely. Same rule |
| `offsetting_factor` | `rca.contribution` | `node_id` | A segment that moved against the KPI, and how much of the move it cancelled |
| `anomaly` | `anomaly.detectors` | period start | An anomalous period *inside the compared windows* — an anomaly two years ago is real but is not evidence about **this** movement |
| `trend` | `anomaly.detectors` | `baseline` | Emitted only when the detector actually said the baseline is drifting. An absent finding is not a finding |
| `execution` | `investigation.execution` | `run` | The run's own cost, promoted from a UI stat into formal evidence |
| `coverage` | `investigation.execution` | `run` | Rows inside vs outside the windows, unreadable rows, dimensions truncated/excluded, segments measured vs suppressed, per-dimension explainability |
| `reconciliation` | `investigation.validation` | `run` | The reconciliation verdict, in full |
| `validation` | `investigation.validation` | check name | One record per quality check, so the checklist is itself evidence |

Segments below the materiality floor produce no `dimension_change` or `contribution` record — a
dimension with fifty values would otherwise yield fifty records saying nothing. The suppressed count
is reported in the `coverage` record, so **nothing goes missing quietly**.

### 5.4 Query provenance, per record

`_node_query()` decides which statement measured a node: depth ≤ 1 came out of the single
all-dimensions breakdown; deeper nodes came out of the drill statement that expanded their *parent*.

Every record then lands in exactly one of two states:

- **Measured** — carries the statement verbatim plus its `query_sequence`.
- **Derived** (`derived=True`) — carries `query: None`, and the provenance check knows not to treat
  that as a gap. Without this flag, every investigation with a drill-down stop reason — which is all
  of them — would carry a permanent warning.

There is no third option. No representative query, no reconstruction.

### 5.5 The confidence ladder

A deterministic ladder rather than a score: every rung is a condition the engine already recorded,
so the rating is explainable and reproducible.

| Rung | Condition |
|---|---|
| `LOW` | Low support, the "other" bucket, no contribution, or a `GROSS_MOVEMENT` basis |
| `MEDIUM` | A truncated dimension, a broad-based change pattern, or a parent leaving more than 5% unexplained |
| `HIGH` | Everything else |

`MAX_UNEXPLAINED_FOR_HIGH_CONFIDENCE = 0.05` is the same materiality floor the ranking uses, so
"material enough to name as a driver" and "material enough to dent confidence" agree.

Anomaly records use their own ladder: a degenerate baseline scale is `LOW` however large the score,
because the baseline barely varied.

### 5.6 Claim wording

Enforced in [constants.py](backend/app/analysis/investigation/constants.py):

- **A contribution is not a cause.** Claims say *contributed*, *moved*, *accounts for*, *offsetting*.
  The word *caused* must never appear.
- `NULL_VALUE_LABEL = "(no value)"` — a real SQL `NULL` is a genuine group and must not read as the
  string `"None"`.
- `number()` writes `65`, not `65.0`; a spurious decimal makes an exact figure look approximate.
  `trim_zeros()` only strips after a decimal point, because stripping unconditionally turns `80`
  into `8`.
- Period labels are written `… to … (exclusive)`, so the half-open window is visible.
- `EVIDENCE_RULES_VERSION` is bumped when a change would make two investigations' evidence read
  differently for identical numbers.

Seven stop-reason codes map to five specification categories. Both are kept: the code is what
actually happened, the category is how it groups in a summary. Mapping rather than collapsing means
nothing is invented and nothing is lost.

---

## 6. Validation

[validation.py](backend/app/analysis/investigation/validation.py) makes two judgements and keeps them
apart on purpose.

### 6.1 Reconciliation — does the decomposition account for the whole movement?

Computed over **every** segment of the chosen dimension, including the residual bucket and the
immaterial ones — never over the primary/secondary/offsetting subsets shown in the UI, which are a
*selection* and are not expected to sum to anything.

It reuses `contribution.contribution_sum` and `tree.tree_drift` rather than reimplementing them,
which is what stops the API verdict and the engine's own warning log ever disagreeing.

| Status | Meaning |
|---|---|
| `PASSED` | The sum is within tolerance of 1.0 |
| `FAILED` | Outside tolerance — **reported, never normalised away**, because re-normalising would hide a lost-rows bug permanently |
| `NOT_APPLICABLE` | There is no decomposition to reconcile. Load-bearing: a `MEDIAN` cannot be decomposed at all, and a missing decomposition is not a failed one |

Under a `GROSS_MOVEMENT` basis the detail says so explicitly — that sum is of magnitudes, not of
signed shares.

**Tree drift has three states, because two causes of drift are legitimate:**

| Status | Meaning |
|---|---|
| `PASSED` | Every node's children sum to it |
| `DRIFT_EXPLAINED` | Each drifting node is a pure split (a child that cannot be scored by deviation-from-proportional) or sits under a truncated level (whose remainder has no residual bucket to hold it) |
| `DRIFT_UNEXPLAINED` | The lost-rows case the engine's warning log always existed for — now a verdict |

`drifting_nodes` carries every drifting node and *why* it drifts, so `DRIFT_EXPLAINED` is auditable
rather than asserted. `Reconciliation.passed` requires both `PASSED` **and** not
`DRIFT_UNEXPLAINED`.

### 6.2 The six quality checks

Fixed order, so the checklist reads the same way on every investigation and two runs are comparable
line by line.

| # | Check | Fails when | Warns when |
|---|---|---|---|
| 1 | `data_period_coverage` | A window starts at or after it ends; a period-over-period comparison is claimed but the previous window has no rows | Windows not contiguous, >50% of rows outside both, or unreadable dates/measures |
| 2 | `numerical_consistency` | Any identity breaks: `change ≠ current − previous`, percent change does not follow, `expected + excess ≠ change`, or one dimension's segments do not reproduce the KPI movement | — |
| 3 | `contribution_reconciliation` | Reconciliation `FAILED` | The level reconciles but a node drifts for no legitimate reason |
| 4 | `query_provenance` | A record cites a statement not in the trace, carries SQL that is not that statement **byte-for-byte**, or the traced RCA count disagrees with what the result reports | A measured record names no statement — and no SQL was invented to fill the gap |
| 5 | `source_traceability` | A record names no dataset, relation or columns, or names a column that is not in what `DESCRIBE` returned | — |
| 6 | `required_metadata` | A record has no claim, no analysis tool, no confidence; a segment type names no dimension or value; a period type does not name both periods | — |

Check 2 compares floats to a relative **and** absolute tolerance of `1e-6`: the values have been
through SQL aggregation and Python arithmetic. Loose enough to survive that, tight enough that a
genuinely wrong number cannot hide behind it. The additivity claim is skipped where the engine
already says it cannot hold — a truncated dimension, or a basis other than `NET_CHANGE` / `MIX_RATE`.

Check 4's byte-identity assertion is the mechanical guard on *never fabricate SQL*: a
plausible-looking statement that was never executed fails here.

Check 5 doubles as **schema-drift detection at the level of an individual claim**.

Every check carries the `inputs` it asserted on, so a reader can redo it.

### 6.3 The verdict

```
any check FAILED                         → FAILED
any WARNING outside COVERAGE_ONLY_CHECKS → WARNING
otherwise                                → VALIDATED
result.state in {NO_DATA, NO_TIME_COLUMN} → NOT_APPLICABLE
```

**Quality fails on a broken identity or missing provenance — never on "the data is thin."** Thin
data describes the *data*, not the analysis, so a coverage-only warning becomes a `caveat` that
leaves the verdict at `VALIDATED`. The airline dataset has 99.98% of its rows outside both compared
windows and still reads as `VALIDATED`, because the analysis of the rows that *do* fall inside is
sound. Thin data surfaces instead as `confidence: low` on the affected records and in the `coverage`
evidence.

### 6.4 Stamping the verdict back onto the records

`apply_verdict()` promotes or demotes every record, because a record left `UNVERIFIED` would mean
the validator never reached it:

- Checks 2 or 6 failed → **every** record `FAILED`.
- Checks 4 or 5 failed → every **measured** record `FAILED`.
- Verdict `NOT_APPLICABLE` → `NOT_APPLICABLE`.
- Otherwise → `VALIDATED`.
- `validation` records are skipped: each already carries its own check's status.

---

## 7. The decision trace

[decisions.py](backend/app/analysis/investigation/decisions.py) records *why the system chose what it
chose*. Every reason is assembled from fields the engines already recorded — a node's contribution
and rank, a dimension's explanatory power, a stop reason, an attribution basis. Nothing is inferred
after the fact, which is what makes the trace an account of what happened rather than a plausible
story about it.

Each `DecisionRecord` carries a `why` sentence **and** the `inputs` it was decided on, so "the
threshold was reached" can always be expanded into *which* threshold and *what* value.

| Kind | Records |
|---|---|
| `period_resolved` | The strategy, grain and anchor — the data's own latest timestamp, not the wall clock — and whether an incomplete newest bucket was excluded |
| `basis_selected` | Why contributions mean what they mean. Worth its own decision because the basis changes the interpretation of every contribution on the page |
| `pattern_classified` | Why the change is concentrated, broad-based or offsetting. Necessary rather than decorative: a broad-based verdict deliberately clears the driver lists, so without this an empty result looks like a failure |
| `dimension_selected` | Which dimension was descended, **and what it was chosen instead of** — the runners-up are not on the result, so the engine records these as it makes them |
| `segment_selected` | Why each named driver was named: contribution, rank, absolute change, NEW/GONE status |
| `driver_suppressed` | A segment material by share but demoted for resting on too few rows |
| `drilldown_stopped` | Which branch ended and **which specific limit ended it** — `_threshold_for()` names the value compared against, including the `max_tree_depth` *this run* applied rather than the package default |

`record_all()` derives everything except the dimension choices, so the RCA engine stays free of
evidence-layer concerns.

---

## 8. The audit trail

Sixteen event types, in order, from `investigation_started` to
`investigation_completed` / `investigation_partial` / `investigation_failed`.

Events carry **`elapsed_ms` — a monotonic offset from the run's start** — rather than a wall-clock
timestamp. Two runs over the same data then produce the same trail, which is what "reproducible" has
to mean for an audit log. The service turns the offset into `occurred_at` for display; the offset is
what makes the trail comparable.

Elapsed times are the run's total rather than per-step. The engine measures itself as a whole, and
inventing plausible per-step splits would be exactly the kind of fabrication this layer exists to
prevent.

---

## 9. The tree as an evidence graph

[graph.py](backend/app/analysis/investigation/graph.py) turns the RCA tree into a serialisable
document where every node names the evidence records supporting it and the decisions that produced
it. Possible only because evidence ids are derived from content.

- `evidence_ids` — every record whose `node_id` matches.
- The synthetic root stands for the KPI as a whole and its `node_id` is empty, so no segment record
  points at it. The `kpi_change`, `comparison` and `reconciliation` records are attached to it
  explicitly — otherwise the one node every reader looks at first would be the only one with no
  evidence behind it.
- `decision_sequences` — every decision made about that node.
- `path` uses the same shape as `DriverRead.path`, so a consumer reading either payload never has to
  branch on it.
- `node_key` is a short, opaque, DOM-safe handle: `node_id` is built by joining `dimension=value`
  pairs from user data, so it can contain `|`, `=`, spaces and `#`. Hashed with `blake2b`, **not**
  the built-in `hash()` — that one is salted per process for strings, so the same tree would get
  different keys on every run and an anchor link would break on reload.

`stamp_decisions()` attaches the evidence ids for the node each decision was about, so a decision
can be read alongside the numbers it was made on.

---

## 10. Outcome status

| Status | Meaning |
|---|---|
| `COMPLETED` | Nothing was skipped or degraded |
| `PARTIAL` | **Not a failure.** The decomposition succeeded and something optional did not |
| `FAILED` | There is no result at all |

`PARTIAL` means *"trust what is here, and here is what is missing."* It is reached when there are any
limitations, when quality is `FAILED`, or when the tree drifts unexplained. Every reason lands in
`limitations`, in the reader's terms:

- a dimension was excluded, or truncated so its remainder cannot be itemised;
- drill-down nodes do not sum to their parent for any legitimate reason;
- quality checks failed, so findings are reported but not validated;
- anomaly detection could not run — no detectable reporting frequency, or an `AppError` from the
  detector.

A terminal analysis state is **not** a failure. No data, no previous period, no change and
unattributable are all *results*, correctly reached.

Anomaly detection is deliberately bounded: it runs on the same connection and relation (the two
engines project into differently named temp tables), and its failure is a limitation, never a 5xx.
Turning a usable investigation into a 500 because an optional step could not run would be the wrong
trade.

---

## 11. Persistence

### 11.1 Four tables, not one JSON blob

Because the read patterns differ. Migration
[0003](backend/app/db/migrations/versions/0003_investigation_layer.py) is hand-written rather than
autogenerated, because autogenerate flattens `JSON().with_variant(JSONB, "postgresql")` to plain
`JSON` and would silently drop JSONB on PostgreSQL.

| Table | Why it is its own table |
|---|---|
| `investigations` | Fetched whole. Execution metrics and verdicts are **typed columns**, not JSON, because the evidence panel shows them first and they are worth filtering on ("every failed reconciliation this week") |
| `investigation_evidence` | Filtered, paged, and addressable by its own id. `company_id` is denormalised onto the row so `GET /api/evidence/{id}` — whose path carries no dataset — is one indexed read |
| `investigation_queries` | The SQL text is the largest thing in the record and must not be dragged along by every read. **No `parameters` column, deliberately** |
| `investigation_audit_events` | Worth querying across investigations |

What nothing ever filters *inside* — the findings payload, the tree, the decision trace, the quality
checks, limitations, notices — stays in JSON.

Enums are plain `str` columns guarded by `CHECK` constraints rather than native PostgreSQL `ENUM`
types, so adding a value stays a data-only change instead of an `ALTER TYPE` migration.

`investigation_evidence` has no `updated_at`: rows are written once with their investigation and
never updated.

### 11.2 The lifecycle

Committed in **stages** — `PLANNED`, then `RUNNING`, then a terminal status — so the status is
observable rather than merely asserted. A status that only ever exists inside one transaction is not
a persisted status.

Everything is resolved and gated *before* any row is written: a request that fails the readiness
check must not leave a `FAILED` investigation behind, or the history fills up with rejected requests.

A failed run **is** persisted, and the typed error still propagates to the client. An investigation
that vanishes on failure has no audit trail and makes `FAILED` dead vocabulary.

`reconcile_stale_investigations()` runs at startup from [main.py](backend/app/main.py): an
investigation runs synchronously inside one request, so a row still `RUNNING` after
`INVESTIGATION_STALE_MINUTES` means the process died mid-flight.

### 11.3 Reuse, not caching

`POST /api/investigations` returns an existing completed investigation — 200 instead of 201 — when
one would compute exactly this again. The de-dup key is `(dataset, kpi_definition, max_drivers,
max_tree_depth, engine_version)`, plus the guard that the run **finished after** both the dataset and
the definition were last changed.

A result *cache* is deliberately **not** built. A stale hit would attach a real query trace to
numbers that trace did not produce — precisely the fabrication this layer exists to prevent. The
persisted run *is* the cache, and it is exact by construction rather than a guess about staleness.
`?refresh=true` forces a fresh run.

`ENGINE_VERSION` is bumped when a change to the engines or the builder would make two investigations
of the same data non-comparable. The de-dup key includes it, so a bump invalidates reuse rather than
silently serving a stale shape.

---

## 12. API surface

```
POST   /api/investigations                   {dataset_id, question?} -> 201 + Location (200 on reuse)
GET    /api/investigations                   ?dataset_id= &status=   -> paged history
GET    /api/investigations/{id}              findings, verdicts, decisions — no detail
GET    /api/investigations/{id}/evidence     ?type= repeatable       -> paged records
GET    /api/investigations/{id}/tree         the hierarchy, evidence ids per node
GET    /api/investigations/{id}/queries      every statement, verbatim
GET    /api/investigations/{id}/audit        what happened, in order
GET    /api/evidence/{id}                    one record, addressable on its own
```

Two routers because the specification has two roots: an evidence record must be addressable without
knowing which investigation produced it.

`GET /api/investigations/{id}` deliberately carries **no** evidence list, query trace or audit trail
— those are the largest parts of the record, and their counts are included so the UI can label and
enable those actions without fetching them.

An unrecognised `?type=` is rejected as `INVALID_FILTER` rather than matching no rows: "no evidence
of that kind" is a different claim from "that is not a kind of evidence."

Cross-company access returns **404**, not 403.

### `/api/investigations` vs `/api/rca/investigations`

| | `/api/rca/investigations` | `/api/investigations` |
|---|---|---|
| What it is | **The analysis** | **The investigation** |
| Persisted | No — creates nothing | Yes — addressable by id |
| Status | 200 | 201 (200 on reuse) |
| A link to it | An instruction to recompute | A snapshot |

Both are kept deliberately. The first is what the existing UI and test suite depend on; retiring it
belongs in its own change.

---

## 13. Frontend

[features/rca/evidence/](frontend/src/features/rca/evidence/) — a native `<details>`, **open by
default**, on the investigation page. The specification asks for collapsible, not collapsed, and the
evidence records are anchor targets for the hierarchy's links. A native disclosure brings keyboard
operation, correct AT semantics, find-in-page expansion and fragment-navigation expansion for free —
all of which matter more on an audit surface than anywhere else.

| Component | What it shows |
|---|---|
| [evidence-section.tsx](frontend/src/features/rca/evidence/evidence-section.tsx) | The shell: quality pill plus rows scanned / compared / queries / reconciliation in the summary |
| [evidence-quality.tsx](frontend/src/features/rca/evidence/evidence-quality.tsx) | The verdict pill and the six-check checklist |
| [execution-evidence.tsx](frontend/src/features/rca/evidence/execution-evidence.tsx) | What the analysis was built from |
| [dimension-explainability.tsx](frontend/src/features/rca/evidence/dimension-explainability.tsx) | How differently each dimension's segments behaved — **its own block, one-way bars, an off-scale marker** |
| [decision-trace.tsx](frontend/src/features/rca/evidence/decision-trace.tsx) | Why the system chose what it chose; also exports the stop-reason labels `rca-tree.tsx` reuses |
| [evidence-records.tsx](frontend/src/features/rca/evidence/evidence-records.tsx) | Every claim. Each is an anchor target, so the tree links to the claim behind a node without becoming an interactive widget |
| [query-trace.tsx](frontend/src/features/rca/evidence/query-trace.tsx) | *"Seven queries in 141 ms"*, made inspectable. Fetched on first open, never on page load |
| [investigation-source.tsx](frontend/src/features/rca/evidence/investigation-source.tsx) | What this analysis read |
| [audit-trail.tsx](frontend/src/features/rca/evidence/audit-trail.tsx) | What happened, in order. Lazy — a modal for twelve timestamps is over-engineering |

Two deliberate UI rules:

- **Evidence quality is kept visually quiet.** Severity — how big the KPI move was — owns the loud
  full-width alert band. A failed check must not compete with a `CRITICAL` KPI for attention. Never
  style it with `alert-danger`.
- **The quality pill is not `ValidationPill`.** That one is typed on the *dataset schema* verdict.
  Widening it would invite "the file is valid" to be read as "the analysis is trustworthy" — the
  exact conflation this section exists to prevent.

Nothing is derived client-side that the backend owns: the reconciliation tick comes only from the
backend's verdict, never from `contribution_sum` and a duplicated tolerance.

`createInvestigation` is deliberately **not** wrapped in `cache()` — it is a mutation, and `cache()`
would make two calls in one render silently return the same row.

---

## 14. Configuration

| Setting | Default | Notes |
|---|---|---|
| `INVESTIGATION_RECONCILIATION_TOLERANCE` | `1e-6` | The **reporting** band for the verdict. The value actually applied is persisted on every row, so a raised tolerance shows up in the record rather than only in the environment |
| `INVESTIGATION_STALE_MINUTES` | `10` | A row still `RUNNING` after this long means the process died mid-request |

**Two tolerances, because they answer different questions.**
`rca.constants.CONTRIBUTION_SUM_TOLERANCE` is the correctness invariant — *did we lose rows?* — and is
deliberately **not** tunable: a tunable one would let an operator configure a lost-rows bug into a
green tick. The setting above is the reporting band only.

---

## 15. Invariants — do not break these

1. **Never fabricate SQL.** A measured record carries the statement verbatim; a derived record
   carries `None`. Check 4 asserts byte-identity against the trace, which makes this mechanical
   rather than aspirational.
2. **Never store a bound parameter.** `QueryRecord` has no field for one, by construction.
3. **A contribution is not a cause.** *Contributed*, *moved*, *accounts for*. Never *caused*.
4. **A contribution is not explainability.** Separate columns, separate evidence types, separate UI
   blocks with different bar shapes. A contribution is a share of the change and cannot exceed 100%;
   explainability measures deviation from proportional movement, so segments moving in opposite
   directions add to it without adding to the net change. The airline fixture reports 132% and that
   is correct.
5. **Never re-normalise a failing reconciliation.** It would hide a lost-rows bug permanently.
6. **Quality never fails on thin data.** That is a caveat plus low confidence, not a verdict.
7. **`NOT_APPLICABLE` is a real answer.** A `MEDIAN` cannot be decomposed; a missing decomposition is
   not a failed one.
8. **The evidence namespace and the id digest are persisted contract.** Never change either.
9. **This layer computes nothing analytical.** If a number is not already on an engine result, it
   does not belong in evidence.
10. **The pure package imports no SQLAlchemy, no Pydantic and no storage.** The service is the only
    layer that reads the database.

---

## 16. Tests

| File | Count | Covers |
|---|---|---|
| [test_investigation_api.py](backend/tests/integration/test_investigation_api.py) | 41 | The resource contract, lifecycle and audit trail, evidence, reconciliation and quality, the decision trace, the query trace, the tree as a graph, edge cases, tenant isolation on every endpoint, and the acceptance scenario end to end |
| [test_query_trace.py](backend/tests/unit/test_query_trace.py) | 14 | The tracer alone and inside the engine — including that a filtered KPI's filter value appears in **no** stored SQL and no record attribute |
| [test_investigation_fixture.py](backend/tests/unit/test_investigation_fixture.py) | 9 | Re-derives the airline fixture's marginals from the CSV, so a hand edit that breaks the scenario fails loudly |

Assertions worth knowing about, because they pin decisions rather than behaviour:

- Evidence ids, node keys and stop reasons are **identical across two runs**.
- Every measured claim names the statement that produced it, and that statement is in the trace.
- `source_columns` name columns that really exist in the relation that was read.
- The stored relation is a storage key, never a server temp path.
- NEW and GONE segments carry no mechanical percentage change.
- Contribution and explainability never share a record.
- The displayed driver subsets need **not** sum to the whole; the complete decomposition does.
- A `MEDIAN` KPI reconciles as `NOT_APPLICABLE`, not `FAILED`.
- All six checks are reported, in a fixed order.
- Reading an investigation does not drag the SQL along.
- A KPI that did not change still produces accounting evidence.
- The root-cause pass costs exactly seven statements.

---

## 17. How to extend it

**Adding an evidence type**

1. Add the value to `EvidenceType` in [enums.py](backend/app/db/models/enums.py) — the `CHECK`
   constraint is generated from the enum, so add a migration that rewrites it.
2. Add it to `MEASURED_TYPES`, `SEGMENT_TYPES` and/or `PERIOD_TYPES` in
   [constants.py](backend/app/analysis/investigation/constants.py) if check 4 or 6 should apply.
3. Write a builder method on `_Builder` and call it from `build()`. Pick a stable `key` — it feeds
   the id.
4. Cite a `Purpose` via `self.queries.find(...)` if the number was measured; pass `derived=True` if
   it was not.
5. Add a label in [evidence-records.tsx](frontend/src/features/rca/evidence/evidence-records.tsx).
6. Bump `EVIDENCE_RULES_VERSION` if existing claims would now read differently.

**Adding a quality check**

1. Name it as a `CHECK_*` constant and add it to `QUALITY_CHECK_ORDER` — the order is part of the
   contract.
2. Write `_check_*()` returning a `QualityCheck` with the `inputs` it asserted on.
3. Register it in the `checks` dict in `assess()`.
4. Decide whether it belongs in `COVERAGE_ONLY_CHECKS` (describes the data → caveat) or not
   (describes the analysis → degrades the verdict).
5. Decide whether `apply_verdict()` should demote records when it fails, and which ones.
6. It becomes a `validation` evidence record automatically — the checklist is itself evidence.

**Tracing a new statement**

Add a `Purpose`, pass it to `tracer.execute(...)`, and add a user-facing label to `PURPOSE_LABELS` in
[query-trace.tsx](frontend/src/features/rca/evidence/query-trace.tsx). If the RCA statement count
changes, `test_the_golden_investigation_traces_seven_statements` will tell you.
