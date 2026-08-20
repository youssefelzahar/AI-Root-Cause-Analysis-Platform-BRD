"""Root cause analysis engine (PRD section 14).

Answers "why did this KPI change?" by decomposing a period-over-period change
across the KPI's dimensions into ranked, evidence-backed contributing factors
and a hierarchical tree.

Layering, mirroring the rest of ``app.analysis``:

* ``models``, ``constants``          - value types and every named threshold
* ``contribution``, ``ranking``, ``tree`` - pure maths; import only models + stdlib
* ``casting``, ``period_analysis``, ``dimension_analysis`` - build SQL strings; never execute
* ``engine``                         - the only module that touches a connection

Nothing here imports SQLAlchemy or an ORM model, so the engine is testable
against a bare DuckDB connection with no database in sight.
"""

from app.analysis.rca.engine import run_investigation, verify_tree
from app.analysis.rca.models import RcaResult, RcaSpec

__all__ = ["RcaResult", "RcaSpec", "run_investigation", "verify_tree"]
