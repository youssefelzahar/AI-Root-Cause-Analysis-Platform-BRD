"""All ORM models.

Importing every model here is what makes ``Base.metadata`` complete for both
Alembic autogenerate and ``create_all`` in the test fixtures.
"""

from app.db.models.base import Base, JSONColumn, TimestampMixin, UUIDPkMixin
from app.db.models.company import Company
from app.db.models.dataset import Dataset
from app.db.models.investigation import (
    ENGINE_VERSION,
    EvidenceRecord,
    Investigation,
    InvestigationAuditEvent,
    InvestigationQuery,
)
from app.db.models.kpi import KpiDefinition
from app.db.models.profile import ColumnProfile, DatasetProfile
from app.db.models.sql_connection import SqlConnection
from app.db.models.user import User
from app.db.models.validation import RULES_VERSION, SchemaValidation

__all__ = [
    "ENGINE_VERSION",
    "RULES_VERSION",
    "Base",
    "ColumnProfile",
    "Company",
    "Dataset",
    "DatasetProfile",
    "EvidenceRecord",
    "Investigation",
    "InvestigationAuditEvent",
    "InvestigationQuery",
    "JSONColumn",
    "KpiDefinition",
    "SchemaValidation",
    "SqlConnection",
    "TimestampMixin",
    "UUIDPkMixin",
    "User",
]
