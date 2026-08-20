"""Render the Phase 1 database ERD to a vector PDF.

Writes the PDF by hand rather than pulling in matplotlib/reportlab/graphviz:
the diagram is a few hundred rectangles, lines and text runs, and none of those
libraries is otherwise a dependency of this project. Output is real vector
content with selectable text, not a rasterised image.

    python scripts/generate_erd.py

The schema below mirrors backend/app/db/models/. When a model changes, update
the TABLES/EDGES definitions here in the same commit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "database-erd.pdf"

PAGE_W, PAGE_H = 1560.0, 940.0

# --- palette ---------------------------------------------------------------
INK = (0.12, 0.14, 0.17)
MUTED = (0.42, 0.46, 0.52)
LINE = (0.78, 0.80, 0.84)
PAPER = (1.0, 1.0, 1.0)
BAND = (0.972, 0.976, 0.982)
WHITE = (1.0, 1.0, 1.0)

TEAL = (0.11, 0.47, 0.51)
BLUE = (0.13, 0.29, 0.55)
GREEN = (0.16, 0.44, 0.30)
AMBER = (0.64, 0.44, 0.10)
PURPLE = (0.36, 0.24, 0.52)
MAROON = (0.55, 0.20, 0.24)

HEADER_H = 22.0
ROW_H = 13.0

# --- Helvetica advance widths (AFM, /1000 em) ------------------------------
_W = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191,
    "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278,
    ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015,
    "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556, "`": 333,
    "{": 334, "|": 260, "}": 334, "~": 584,
    "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722,
    "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722, "O": 778, "P": 667,
    "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944, "X": 667,
    "Y": 667, "Z": 611,
    "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556, "h": 556,
    "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556, "o": 556, "p": 556,
    "q": 556, "r": 333, "s": 500, "t": 278, "u": 556, "v": 500, "w": 722, "x": 500,
    "y": 500, "z": 500,
}
for _d in "0123456789":
    _W[_d] = 556


def text_width(s: str, size: float, bold: bool = False, mono: bool = False) -> float:
    if mono:
        return len(s) * 0.6 * size
    total = sum(_W.get(ch, 556) for ch in s) / 1000.0 * size
    return total * 1.06 if bold else total


# --- schema ----------------------------------------------------------------
# (name, type, key)  key in {"PK", "FK", ""}
TABLES: dict[str, dict] = {
    "companies": {
        "x": 40, "y": 110, "w": 300, "color": TEAL, "note": "tenant root",
        "cols": [
            ("id", "uuid", "PK"),
            ("name", "varchar(200)", ""),
            ("slug", "varchar(100) U", ""),
            ("created_at", "timestamptz", ""),
            ("updated_at", "timestamptz", ""),
        ],
    },
    "users": {
        "x": 40, "y": 250, "w": 300, "color": TEAL, "note": "no credentials - see note 1",
        "cols": [
            ("id", "uuid", "PK"),
            ("company_id", "uuid", "FK"),
            ("email", "varchar(320)", ""),
            ("display_name", "varchar(200)", ""),
            ("is_active", "boolean", ""),
            ("created_at", "timestamptz", ""),
            ("updated_at", "timestamptz", ""),
        ],
    },
    "sql_connections": {
        "x": 40, "y": 430, "w": 300, "color": MAROON, "note": "PRD 8",
        "cols": [
            ("id", "uuid", "PK"),
            ("company_id", "uuid", "FK"),
            ("created_by", "uuid", "FK"),
            ("name", "varchar(150)", ""),
            ("host", "varchar(255)", ""),
            ("port", "integer", ""),
            ("database_name", "varchar(128)", ""),
            ("username", "varchar(128)", ""),
            ("password_encrypted", "text", ""),
            ("encrypt", "boolean", ""),
            ("trust_server_certificate", "boolean", ""),
            ("last_tested_at", "timestamptz", ""),
            ("last_test_ok", "boolean", ""),
            ("last_test_error", "varchar(500)", ""),
            ("is_active", "boolean", ""),
            ("created_at", "timestamptz", ""),
            ("updated_at", "timestamptz", ""),
        ],
    },
    "datasets": {
        "x": 430, "y": 180, "w": 310, "color": BLUE, "note": "PRD 7 - id doubles as storage UUID",
        "cols": [
            ("id", "uuid", "PK"),
            ("company_id", "uuid", "FK"),
            ("user_id", "uuid", "FK"),
            ("name", "varchar(255)", ""),
            ("description", "text", ""),
            ("source_type", "varchar(20)", ""),
            ("original_filename", "varchar(255)", ""),
            ("file_format", "varchar(20)", ""),
            ("storage_key", "varchar(512)", ""),
            ("normalized_key", "varchar(512)", ""),
            ("size_bytes", "bigint", ""),
            ("checksum_sha256", "varchar(64)", ""),
            ("row_count", "bigint", ""),
            ("column_count", "integer", ""),
            ("schema_version", "integer", ""),
            ("upload_status", "varchar(20)", ""),
            ("status", "varchar(30)", ""),
            ("quality_state", "varchar(10)", ""),
            ("ingest_options", "jsonb", ""),
            ("source_connection_id", "uuid", "FK"),
            ("source_query", "text", ""),
            ("error_code", "varchar(50)", ""),
            ("error_message", "text", ""),
            ("profiling_started_at", "timestamptz", ""),
            ("profiling_completed_at", "timestamptz", ""),
            ("deleted_at", "timestamptz", ""),
            ("created_at", "timestamptz", ""),
            ("updated_at", "timestamptz", ""),
        ],
    },
    "dataset_profiles": {
        "x": 830, "y": 110, "w": 300, "color": GREEN, "note": "PRD 9 - dataset level",
        "cols": [
            ("id", "uuid", "PK"),
            ("dataset_id", "uuid U", "FK"),
            ("profile_version", "integer", ""),
            ("row_count", "bigint", ""),
            ("column_count", "integer", ""),
            ("file_size_bytes", "bigint", ""),
            ("duplicate_row_count", "bigint", ""),
            ("duplicate_row_pct", "float", ""),
            ("duplicate_check_skipped", "boolean", ""),
            ("missing_cell_count", "bigint", ""),
            ("missing_cell_pct", "float", ""),
            ("quality_status", "varchar(20)", ""),
            ("engine", "varchar(20)", ""),
            ("exact_quantiles", "boolean", ""),
            ("duration_ms", "integer", ""),
            ("generated_at", "timestamptz", ""),
            ("extra", "jsonb", ""),
            ("created_at", "timestamptz", ""),
            ("updated_at", "timestamptz", ""),
        ],
    },
    "schema_validations": {
        "x": 830, "y": 560, "w": 300, "color": AMBER, "note": "PRD 10 - append only",
        "cols": [
            ("id", "uuid", "PK"),
            ("dataset_id", "uuid", "FK"),
            ("kpi_definition_id", "uuid", "FK"),
            ("mode", "varchar(20)", ""),
            ("state", "varchar(10)", ""),
            ("error_count", "integer", ""),
            ("warning_count", "integer", ""),
            ("info_count", "integer", ""),
            ("issues", "jsonb", ""),
            ("rules_version", "varchar(20)", ""),
            ("created_at", "timestamptz", ""),
            ("updated_at", "timestamptz", ""),
        ],
    },
    "column_profiles": {
        "x": 1220, "y": 110, "w": 300, "color": GREEN, "note": "PRD 9 - one row per column",
        "cols": [
            ("id", "uuid", "PK"),
            ("dataset_profile_id", "uuid", "FK"),
            ("dataset_id", "uuid", "FK"),
            ("column_name", "varchar(255)", ""),
            ("ordinal_position", "integer", ""),
            ("raw_type", "varchar(50)", ""),
            ("inferred_type", "varchar(20)", ""),
            ("semantic_type", "varchar(20)", ""),
            ("conversion_confidence", "float", ""),
            ("requires_conversion", "boolean", ""),
            ("invalid_value_count", "bigint", ""),
            ("sample_invalid_values", "jsonb", ""),
            ("null_count", "bigint", ""),
            ("null_pct", "float", ""),
            ("unique_count", "bigint", ""),
            ("unique_pct", "float", ""),
            ("min_value", "varchar(255)", ""),
            ("max_value", "varchar(255)", ""),
            ("mean", "float", ""),
            ("median", "float", ""),
            ("stddev", "float", ""),
            ("outlier_count", "bigint", ""),
            ("outlier_lower", "float", ""),
            ("outlier_upper", "float", ""),
            ("percentiles", "jsonb", ""),
            ("top_values", "jsonb", ""),
            ("datetime_stats", "jsonb", ""),
            ("kpi_measure_score", "float", ""),
            ("kpi_dimension_score", "float", ""),
            ("kpi_time_score", "float", ""),
            ("suggested_aggregation", "varchar(20)", ""),
            ("candidate_reasons", "jsonb", ""),
        ],
    },
    "kpi_definitions": {
        "x": 1220, "y": 600, "w": 300, "color": PURPLE, "note": "PRD 11 - RCA contract",
        "cols": [
            ("id", "uuid", "PK"),
            ("dataset_id", "uuid", "FK"),
            ("company_id", "uuid", ""),
            ("created_by", "uuid", "FK"),
            ("name", "varchar(150)", ""),
            ("column_name", "varchar(255)", ""),
            ("aggregation", "varchar(20)", ""),
            ("time_column", "varchar(255)", ""),
            ("dimensions", "jsonb", ""),
            ("comparison", "varchar(30)", ""),
            ("comparison_config", "jsonb", ""),
            ("filters", "jsonb", ""),
            ("definition", "jsonb", ""),
            ("is_active", "boolean", ""),
            ("validation_state", "varchar(10)", ""),
            ("created_at", "timestamptz", ""),
            ("updated_at", "timestamptz", ""),
        ],
    },
}

# (from, to, cardinality, on-delete, waypoints, dashed)
EDGES = [
    ("companies", "users", "1:N", "CASCADE", [(190, 197), (190, 250)], False),
    ("companies", "sql_connections", "1:N", "CASCADE",
     [(40, 153), (22, 153), (22, 551), (40, 551)], False),
    ("companies", "datasets", "1:N", "CASCADE",
     [(340, 153), (385, 153), (385, 213), (430, 213)], False),
    ("users", "datasets", "1:N", "SET NULL",
     [(340, 306), (403, 306), (403, 265), (430, 265)], False),
    ("users", "sql_connections", "1:N", "SET NULL", [(290, 363), (290, 430)], False),
    ("sql_connections", "datasets", "1:N", "SET NULL",
     [(340, 551), (388, 551), (388, 520), (430, 520)], False),
    ("users", "kpi_definitions", "1:N", "SET NULL",
     [(40, 340), (14, 340), (14, 892), (1370, 892), (1370, 843)], True),
    ("datasets", "dataset_profiles", "1:1", "CASCADE",
     [(740, 244), (785, 244), (785, 244), (830, 244)], False),
    ("datasets", "schema_validations", "1:N", "CASCADE",
     [(740, 470), (790, 470), (790, 649), (830, 649)], False),
    ("datasets", "kpi_definitions", "1:N", "CASCADE",
     [(740, 540), (806, 540), (806, 782), (1220, 782)], False),
    ("datasets", "column_profiles", "1:N", "CASCADE",
     [(585, 180), (585, 88), (1370, 88), (1370, 110)], True),
    ("dataset_profiles", "column_profiles", "1:N", "CASCADE",
     [(1130, 300), (1220, 300)], False),
    ("kpi_definitions", "schema_validations", "1:N", "CASCADE",
     [(1220, 660), (1180, 660), (1180, 700), (1130, 700)], False),
]


class Canvas:
    """Accumulates PDF content-stream operators in top-left coordinates."""

    def __init__(self) -> None:
        self.ops: list[str] = []

    # -- coordinate conversion: callers think top-left, PDF is bottom-left --
    @staticmethod
    def _y(y: float) -> float:
        return PAGE_H - y

    def rect(self, x, y, w, h, fill=None, stroke=None, width=0.6):
        if fill:
            self.ops.append(f"{fill[0]:.3f} {fill[1]:.3f} {fill[2]:.3f} rg")
        if stroke:
            self.ops.append(f"{stroke[0]:.3f} {stroke[1]:.3f} {stroke[2]:.3f} RG")
            self.ops.append(f"{width:.2f} w")
        self.ops.append(f"{x:.2f} {self._y(y + h):.2f} {w:.2f} {h:.2f} re")
        if fill and stroke:
            self.ops.append("B")
        elif fill:
            self.ops.append("f")
        else:
            self.ops.append("S")

    def line(self, pts, color=MUTED, width=0.9, dashed=False):
        self.ops.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG")
        self.ops.append(f"{width:.2f} w")
        self.ops.append("[3 2] 0 d" if dashed else "[] 0 d")
        x0, y0 = pts[0]
        self.ops.append(f"{x0:.2f} {self._y(y0):.2f} m")
        for x, y in pts[1:]:
            self.ops.append(f"{x:.2f} {self._y(y):.2f} l")
        self.ops.append("S")
        self.ops.append("[] 0 d")

    def dot(self, x, y, r=2.6, color=MUTED):
        k = r * 0.5523
        cy = self._y(y)
        self.ops.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg")
        self.ops.append(f"{x + r:.2f} {cy:.2f} m")
        self.ops.append(f"{x + r:.2f} {cy + k:.2f} {x + k:.2f} {cy + r:.2f} {x:.2f} {cy + r:.2f} c")
        self.ops.append(f"{x - k:.2f} {cy + r:.2f} {x - r:.2f} {cy + k:.2f} {x - r:.2f} {cy:.2f} c")
        self.ops.append(f"{x - r:.2f} {cy - k:.2f} {x - k:.2f} {cy - r:.2f} {x:.2f} {cy - r:.2f} c")
        self.ops.append(f"{x + k:.2f} {cy - r:.2f} {x + r:.2f} {cy - k:.2f} {x + r:.2f} {cy:.2f} c")
        self.ops.append("f")

    def crowsfoot(self, x, y, direction, color=MUTED):
        """Three-pronged 'many' marker pointing back along the edge."""
        d = 7.0
        s = 4.0
        dx, dy = {"L": (-1, 0), "R": (1, 0), "U": (0, -1), "D": (0, 1)}[direction]
        self.ops.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG")
        self.ops.append("0.9 w")
        tips = (
            [(x - d * dx, y - d * dy + s), (x - d * dx, y - d * dy - s), (x - d * dx, y - d * dy)]
            if dx
            else [(x + s, y - d * dy), (x - s, y - d * dy), (x, y - d * dy)]
        )
        for tx, ty in tips:
            self.ops.append(f"{x:.2f} {self._y(y):.2f} m {tx:.2f} {self._y(ty):.2f} l S")

    def text(self, x, y, s, size=8.0, color=INK, bold=False, mono=False, align="left"):
        font = "F3" if mono else ("F2" if bold else "F1")
        if align == "center":
            x -= text_width(s, size, bold, mono) / 2
        elif align == "right":
            x -= text_width(s, size, bold, mono)
        esc = s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        self.ops.append(
            f"BT /{font} {size:.2f} Tf {color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg "
            f"{x:.2f} {self._y(y):.2f} Td ({esc}) Tj ET"
        )

    def stream(self) -> bytes:
        return "\n".join(self.ops).encode("latin-1", "replace")


def table_height(name: str) -> float:
    return HEADER_H + len(TABLES[name]["cols"]) * ROW_H


def draw_table(c: Canvas, name: str) -> None:
    t = TABLES[name]
    x, y, w = t["x"], t["y"], t["w"]
    h = table_height(name)
    cols = t["cols"]

    c.rect(x, y, w, h, fill=PAPER, stroke=LINE, width=0.7)
    c.rect(x, y, w, HEADER_H, fill=t["color"])
    c.text(x + 9, y + 14.5, name, size=10.0, color=WHITE, bold=True)
    c.text(x + w - 9, y + 14.0, f"{len(cols)} cols", size=6.8, color=(0.88, 0.90, 0.92),
           align="right")

    ry = y + HEADER_H
    for i, (col, typ, key) in enumerate(cols):
        if i % 2 == 1:
            c.rect(x + 0.7, ry, w - 1.4, ROW_H, fill=BAND)
        base = ry + 9.0
        if key == "PK":
            c.text(x + 7, base, "PK", size=6.2, color=t["color"], bold=True, mono=True)
        elif key == "FK":
            c.text(x + 7, base, "FK", size=6.2, color=MAROON, bold=True, mono=True)
        c.text(x + 29, base, col, size=7.4, color=INK if key else (0.25, 0.28, 0.33))
        c.text(x + w - 8, base, typ, size=6.4, color=MUTED, mono=True, align="right")
        ry += ROW_H

    c.text(x, y - 5, t["note"], size=6.6, color=MUTED)


def draw_edges(c: Canvas) -> None:
    for src, dst, card, ondelete, pts, dashed in EDGES:
        colour = (0.62, 0.66, 0.72) if dashed else MUTED
        c.line(pts, color=colour, width=0.9, dashed=dashed)

        # "one" end: a filled dot at the parent.
        c.dot(pts[0][0], pts[0][1], r=2.4, color=colour)

        # "many" end: crow's foot at the child, oriented from the final segment.
        (px, py), (qx, qy) = pts[-2], pts[-1]
        if abs(qx - px) >= abs(qy - py):
            direction = "R" if qx > px else "L"
        else:
            direction = "D" if qy > py else "U"
        if card == "1:1":
            c.dot(qx, qy, r=2.4, color=colour)
        else:
            c.crowsfoot(qx, qy, direction, color=colour)

        # Label on the longest segment so it never lands on a corner.
        best, blen = None, -1.0
        for a, b in zip(pts, pts[1:]):
            seg = abs(b[0] - a[0]) + abs(b[1] - a[1])
            if seg > blen:
                best, blen = (a, b), seg
        (ax, ay), (bx, by) = best
        mx, my = (ax + bx) / 2, (ay + by) / 2
        label = f"{card}  {ondelete}"
        tw = text_width(label, 6.2)
        c.rect(mx - tw / 2 - 3, my - 6.5, tw + 6, 10, fill=PAPER)
        c.text(mx, my + 0.6, label, size=6.2, color=MUTED, align="center")


def page_one() -> bytes:
    c = Canvas()
    c.rect(0, 0, PAGE_W, PAGE_H, fill=WHITE)

    c.text(40, 46, "AI Root Cause Analysis Platform", size=19, color=INK, bold=True)
    c.text(40, 64, "Phase 1 - Data Foundation  |  entity relationship diagram", size=9.2,
           color=MUTED)
    c.text(PAGE_W - 40, 46, "8 tables  |  13 foreign keys", size=9, color=MUTED, align="right")
    c.text(PAGE_W - 40, 62, "generated from backend/app/db/models/", size=7.6, color=MUTED,
           align="right", mono=True)
    c.line([(40, 76), (PAGE_W - 40, 76)], color=LINE, width=0.8)

    draw_edges(c)
    for name in TABLES:
        draw_table(c, name)

    # Legend
    ly = 906
    c.text(40, ly, "Legend", size=7.6, color=INK, bold=True)
    items = [
        ("PK", "primary key", MAROON),
        ("FK", "foreign key", MAROON),
        ("U", "unique constraint", MUTED),
    ]
    lx = 90
    for tag, meaning, col in items:
        c.text(lx, ly, tag, size=6.6, color=col, bold=True, mono=True)
        c.text(lx + 18, ly, meaning, size=6.8, color=MUTED)
        lx += 30 + text_width(meaning, 6.8)
    c.line([(lx + 4, ly - 3), (lx + 26, ly - 3)], color=MUTED, width=0.9)
    c.dot(lx + 4, ly - 3, r=2.2)
    c.crowsfoot(lx + 26, ly - 3, "R")
    c.text(lx + 36, ly, "one-to-many", size=6.8, color=MUTED)
    lx += 36 + text_width("one-to-many", 6.8) + 16
    c.line([(lx, ly - 3), (lx + 22, ly - 3)], color=(0.62, 0.66, 0.72), width=0.9, dashed=True)
    c.text(lx + 30, ly, "denormalised / cross-cutting reference", size=6.8, color=MUTED)

    return c.stream()


NOTES = [
    ("1", "users carries no credentials.",
     "Phase 1 ships without authentication by explicit decision, so there is no password, hash or "
     "session column anywhere in the schema. The table exists to satisfy the PRD's user_id metadata "
     "requirement and to give real auth somewhere to attach later."),
    ("2", "datasets.id doubles as the physical storage UUID.",
     "The storage key is {company_id}/{YYYY}/{MM}/{dataset_id}.{ext}. The original filename is kept "
     "in original_filename as metadata only and never contributes a character to a filesystem path "
     "(PRD principle 2)."),
    ("3", "Two different 'status' concepts, deliberately separated.",
     "datasets.status is the pipeline state (pending_upload, uploaded, validating, profiling, "
     "profiled, analysis_ready, plus terminal upload_failed / profiling_failed / blocked). "
     "dataset_profiles.quality_status is the validation verdict (pass / warning / blocked). The PRD "
     "never enumerated either."),
    ("4", "datasets.quality_state is a denormalised copy.",
     "It mirrors the newest structural schema_validations.state so the dataset list can filter on "
     "quality without a join. schema_validations remains the source of truth."),
    ("5", "column_profiles.dataset_id is denormalised too.",
     "The authoritative parent is dataset_profile_id. Carrying dataset_id as well lets KPI-candidate "
     "queries rank columns without joining through dataset_profiles. Both are ON DELETE CASCADE, so "
     "the two paths cannot disagree."),
    ("6", "Per-column statistics are rows, not a JSON blob.",
     "One row per column lets the KPI candidate query rank in SQL and lets the Columns tab paginate "
     "server-side. Only the type-specific statistics - percentiles, top_values, datetime_stats, "
     "whose shapes differ entirely between numeric, categorical and datetime columns - stay in JSON."),
    ("7", "kpi_definitions stores the contract twice, on purpose.",
     "The typed columns drive validation and queries; definition holds the frozen, source-agnostic "
     "JSON contract handed to the RCA engine in exactly the shape PRD section 11 specifies. The "
     "service is the single writer and builds the JSON from the columns."),
    ("8", "schema_validations is append-only.",
     "Users re-upload corrected files and need to see what changed, so results accumulate rather "
     "than being overwritten. mode distinguishes a structural run from one re-checked against a "
     "specific KPI definition, which is why kpi_definition_id is nullable."),
    ("9", "sql_connections has no plaintext password column.",
     "password_encrypted holds a versioned Fernet token ('v1:' prefix) produced by app.core.security. "
     "The API read schema has no password field at all, so a credential cannot leak through a "
     "response even by mistake."),
    ("10", "JSON columns are portable across dialects.",
     "Every JSON column is declared JSON().with_variant(JSONB, 'postgresql'). Production keeps JSONB; "
     "the test suite runs the identical schema against SQLite."),
]


def page_two() -> bytes:
    c = Canvas()
    c.rect(0, 0, PAGE_W, PAGE_H, fill=WHITE)

    c.text(40, 46, "Schema notes", size=19, color=INK, bold=True)
    c.text(40, 64, "Why the model is shaped the way it is", size=9.2, color=MUTED)
    c.line([(40, 76), (PAGE_W - 40, 76)], color=LINE, width=0.8)

    col_w = (PAGE_W - 80 - 50) / 2
    x, y = 40, 112
    for i, (num, title, body) in enumerate(NOTES):
        if i == 5:
            x, y = 40 + col_w + 50, 112

        c.rect(x, y - 9.5, 15, 13, fill=BLUE)
        c.text(x + 7.5, y, num, size=7.4, color=WHITE, bold=True, align="center")
        c.text(x + 23, y, title, size=8.8, color=INK, bold=True)

        yy = y + 15
        words, line = body.split(), ""
        for word in words:
            trial = f"{line} {word}".strip()
            if text_width(trial, 7.8) > col_w - 23:
                c.text(x + 23, yy, line, size=7.8, color=(0.32, 0.35, 0.40))
                yy += 11
                line = word
            else:
                line = trial
        if line:
            c.text(x + 23, yy, line, size=7.8, color=(0.32, 0.35, 0.40))
            yy += 11
        y = yy + 17

    c.text(40, PAGE_H - 34,
           "Source of truth: backend/app/db/models/  -  regenerate with  python scripts/generate_erd.py",
           size=7.4, color=MUTED, mono=True)
    return c.stream()


def build_pdf() -> bytes:
    streams = [page_one(), page_two()]
    objects: list[bytes] = []

    # 1 catalog, 2 pages, 3/5 pages, 4/6 contents, 7-9 fonts
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>")
    for i, s in enumerate(streams):
        page_no, content_no = 3 + i * 2, 4 + i * 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
            f"/Resources << /Font << /F1 7 0 R /F2 8 0 R /F3 9 0 R >> >> "
            f"/Contents {content_no} 0 R >>".encode()
        )
        objects.append(
            f"<< /Length {len(s)} >>\nstream\n".encode() + s + b"\nendstream"
        )
        assert page_no  # layout guard
    for base in ("Helvetica", "Helvetica-Bold", "Courier"):
        objects.append(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>".encode()
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"

    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def verify_against_models() -> list[str]:
    """Compare the hand-maintained TABLES against SQLAlchemy's live metadata."""
    root = Path(__file__).resolve().parents[1] / "backend"
    code = (
        "import json;"
        "from app.db.models import base;"
        "import app.db.models as m;"
        "print(json.dumps({t.name: sorted(c.name for c in t.columns) "
        "for t in base.Base.metadata.tables.values()}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return [f"could not import models ({proc.stderr.strip().splitlines()[-1:] or ['?']})"]

    import json

    live = json.loads(proc.stdout)
    problems = []
    for name in set(live) | set(TABLES):
        if name not in TABLES:
            problems.append(f"{name}: in models but missing from the diagram")
            continue
        if name not in live:
            problems.append(f"{name}: in the diagram but not in the models")
            continue
        drawn = sorted(c[0] for c in TABLES[name]["cols"])
        if drawn != live[name]:
            missing = sorted(set(live[name]) - set(drawn))
            extra = sorted(set(drawn) - set(live[name]))
            if missing:
                problems.append(f"{name}: missing {', '.join(missing)}")
            if extra:
                problems.append(f"{name}: extra {', '.join(extra)}")
    return problems


if __name__ == "__main__":
    problems = verify_against_models()
    if problems:
        print("Diagram is out of sync with the models:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("Diagram matches backend/app/db/models/ exactly.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(build_pdf())
    print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes)")
    sys.exit(1 if problems else 0)
