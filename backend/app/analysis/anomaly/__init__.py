"""KPI time-series anomaly detection.

Answers "is this KPI behaving unusually compared with its own history?" by
building the KPI's time series, learning a robust baseline from the periods
before each observation, and scoring the departure.

Layering, mirroring ``app.analysis.rca``:

* ``models``, ``constants``   - value types and every named threshold
* ``baseline``, ``scoring``, ``detectors`` - pure maths; import only models + stdlib
* ``series``                  - builds SQL strings; never executes
* ``engine``                  - the only module that touches a connection

Nothing here imports SQLAlchemy or Pydantic, so the engine is testable against a
bare DuckDB connection with no database in sight. It deliberately reuses the RCA
package's casting, filter grammar and calendar maths rather than growing a
second copy of them.
"""

from app.analysis.anomaly.engine import detect_anomalies
from app.analysis.anomaly.models import AnomalyReport, AnomalySpec

__all__ = ["AnomalyReport", "AnomalySpec", "detect_anomalies"]
