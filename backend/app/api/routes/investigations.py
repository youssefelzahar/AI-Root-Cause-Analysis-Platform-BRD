"""Persisted, evidence-backed investigation endpoints.

Two routers because the specification has two roots: an investigation is
addressable under ``/investigations``, and a single evidence record is
addressable under ``/evidence`` so the UI can lazy-load one without knowing which
investigation produced it.

Relationship to ``POST /api/rca/investigations``, which stays as it is:

* ``/api/rca/investigations`` is **the analysis** - stateless, 200, recomputes on
  every call, creates nothing.
* ``/api/investigations`` is **the investigation** - persisted, 201, addressable
  by id, evidence-backed, and a link to it is a snapshot rather than an
  instruction to recompute.

Both are kept deliberately. The first is what the existing UI and test suite
depend on; retiring it belongs in its own change.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import Pagination, RequestContext, get_current_context, get_db, pagination
from app.core.exceptions import ValidationError
from app.db.models.enums import EvidenceType, InvestigationStatus
from app.schemas.common import Page
from app.schemas.investigation import (
    AuditEventRead,
    EvidenceRecordRead,
    InvestigationCreate,
    InvestigationRead,
    InvestigationSummary,
    InvestigationTreeRead,
    QueryTraceRead,
)
from app.services import investigation_service

router = APIRouter(prefix="/investigations", tags=["investigations"])
evidence_router = APIRouter(prefix="/evidence", tags=["investigations"])


def _read(db: Session, investigation) -> InvestigationRead:
    """Assemble the detail view, counting children without loading them."""
    _, query_count = investigation_service.list_queries(db, investigation, limit=1)
    _, audit_count = investigation_service.list_audit(db, investigation, limit=1)
    return InvestigationRead.from_row(
        investigation,
        dataset_name=investigation.dataset.name,
        query_count=query_count,
        audit_event_count=audit_count,
    )


@router.post("", response_model=InvestigationRead, status_code=status.HTTP_201_CREATED)
def create_investigation(
    payload: InvestigationCreate,
    response: Response,
    refresh: bool = Query(
        default=False,
        description=(
            "Force a fresh run instead of reusing an equivalent completed "
            "investigation of unchanged data."
        ),
    ),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> InvestigationRead:
    """Run an investigation and persist it with its evidence.

    201 with a ``Location`` header, because unlike the stateless RCA endpoint
    this genuinely creates an addressable resource.

    When an equivalent investigation already exists over unchanged data it is
    returned as-is with 200. That is the honest reading of "reuse cached
    analytical results": the persisted run *is* the cache, and it is exact by
    construction rather than a guess about staleness.
    """
    investigation, created = investigation_service.create(
        db,
        payload.dataset_id,
        ctx.company_id,
        user_id=ctx.user_id,
        kpi_definition_id=payload.kpi_definition_id,
        max_drivers=payload.max_drivers,
        max_tree_depth=payload.max_tree_depth,
        question=payload.question,
        refresh=refresh,
    )
    response.headers["Location"] = f"/api/investigations/{investigation.id}"
    if not created:
        response.status_code = status.HTTP_200_OK
    return _read(db, investigation)


@router.get("", response_model=Page[InvestigationSummary])
def list_investigations(
    dataset_id: uuid.UUID | None = None,
    investigation_status: str | None = Query(default=None, alias="status"),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[InvestigationSummary]:
    """Investigation history for this company.

    Not in the specification's endpoint list, but the specification does require
    investigations to be persisted - and history nobody can list is history
    nobody can use.
    """
    if investigation_status is not None:
        _require_enum(investigation_status, InvestigationStatus, "status")

    rows, total = investigation_service.list_investigations(
        db,
        ctx.company_id,
        dataset_id=dataset_id,
        status=investigation_status,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(
        items=[InvestigationSummary.from_row(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{investigation_id}", response_model=InvestigationRead)
def get_investigation(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> InvestigationRead:
    """One investigation and its findings.

    Deliberately carries no evidence list, query trace or audit trail: those are
    the largest parts of the record and are fetched from their own endpoints when
    a reader asks for them.
    """
    investigation = investigation_service.get(db, investigation_id, ctx.company_id)
    return _read(db, investigation)


@router.get("/{investigation_id}/evidence", response_model=Page[EvidenceRecordRead])
def list_evidence(
    investigation_id: uuid.UUID,
    evidence_type: list[str] | None = Query(default=None, alias="type"),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[EvidenceRecordRead]:
    """Structured evidence for one investigation, filterable by type."""
    investigation = investigation_service.get(db, investigation_id, ctx.company_id)
    if evidence_type:
        for value in evidence_type:
            _require_enum(value, EvidenceType, "type")

    rows, total = investigation_service.list_evidence(
        db,
        investigation,
        evidence_types=evidence_type,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(
        items=[EvidenceRecordRead.from_row(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{investigation_id}/tree", response_model=InvestigationTreeRead)
def get_tree(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> InvestigationTreeRead:
    """The drill-down hierarchy, with evidence ids on every node."""
    investigation = investigation_service.get(db, investigation_id, ctx.company_id)
    return InvestigationTreeRead(investigation_id=investigation.id, tree=investigation.tree)


@router.get("/{investigation_id}/queries", response_model=Page[QueryTraceRead])
def list_queries(
    investigation_id: uuid.UUID,
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[QueryTraceRead]:
    """Every statement this investigation executed, verbatim.

    Its own endpoint because the SQL text is the largest part of the record and
    is the least often wanted.
    """
    investigation = investigation_service.get(db, investigation_id, ctx.company_id)
    rows, total = investigation_service.list_queries(
        db, investigation, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[QueryTraceRead.from_row(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{investigation_id}/audit", response_model=Page[AuditEventRead])
def list_audit(
    investigation_id: uuid.UUID,
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[AuditEventRead]:
    """What happened during this investigation, in order."""
    investigation = investigation_service.get(db, investigation_id, ctx.company_id)
    rows, total = investigation_service.list_audit(
        db, investigation, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AuditEventRead.from_row(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@evidence_router.get("/{evidence_id}", response_model=EvidenceRecordRead)
def get_evidence(
    evidence_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> EvidenceRecordRead:
    """One evidence record, addressable on its own.

    Scoped by the company denormalised onto the row, so this stays a single
    indexed read: the path carries no dataset to join through.
    """
    record = investigation_service.get_evidence(db, evidence_id, ctx.company_id)
    return EvidenceRecordRead.from_row(record)


def _require_enum(value: str, enum: type, field: str) -> None:
    """Reject an unknown filter value rather than returning nothing.

    An unrecognised ``?type=`` would otherwise match no rows and read as "this
    investigation has no evidence of that kind", which is a different claim.
    """
    allowed = {member.value for member in enum}
    if value not in allowed:
        raise ValidationError(
            f"{value!r} is not a recognised {field}.",
            code="INVALID_FILTER",
            details={field: value, "supported": sorted(allowed)},
        )
