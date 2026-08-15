"""Dataset, profile, validation and KPI endpoints."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, RequestContext, get_current_context, get_db, pagination
from app.core.exceptions import ConflictError, NotFoundError, NotReadyError
from app.db.models import ColumnProfile, Dataset, DatasetProfile, KpiDefinition, SchemaValidation
from app.db.models.enums import DatasetStatus, ValidationMode
from app.schemas.common import Page
from app.schemas.dataset import (
    DatasetDetail,
    DatasetStatusRead,
    DatasetSummary,
    DatasetUpdate,
    PreviewColumn,
    PreviewRead,
)
from app.schemas.kpi import (
    KpiCandidateRead,
    KpiCandidatesRead,
    KpiDefinitionCreate,
    KpiDefinitionEnvelope,
    KpiDefinitionRead,
)
from app.schemas.profile import ColumnProfileRead, DatasetProfileRead, ProfileEnvelope
from app.schemas.validation import SchemaValidationRead
from app.services import dataset_service, kpi_service
from app.services.jobs import enqueue_profiling

router = APIRouter(prefix="/datasets", tags=["datasets"])


# --- helpers -------------------------------------------------------------
def _profile_for(db: Session, dataset: Dataset) -> DatasetProfile | None:
    return db.scalar(select(DatasetProfile).where(DatasetProfile.dataset_id == dataset.id))


def _active_kpi(db: Session, dataset: Dataset) -> KpiDefinition | None:
    return db.scalar(
        select(KpiDefinition).where(
            KpiDefinition.dataset_id == dataset.id, KpiDefinition.is_active.is_(True)
        )
    )


def to_detail(db: Session, dataset: Dataset) -> DatasetDetail:
    detail = DatasetDetail.model_validate(dataset)
    detail.has_profile = _profile_for(db, dataset) is not None
    detail.has_kpi_definition = _active_kpi(db, dataset) is not None
    detail.analysis_ready = dataset.status == DatasetStatus.ANALYSIS_READY.value
    return detail


def build_status(db: Session, dataset: Dataset) -> DatasetStatusRead:
    latest_validation = db.scalar(
        select(SchemaValidation)
        .where(SchemaValidation.dataset_id == dataset.id)
        .order_by(SchemaValidation.created_at.desc())
        .limit(1)
    )
    return DatasetStatusRead(
        dataset_id=dataset.id,
        status=dataset.status,
        upload_status=dataset.upload_status,
        quality_state=dataset.quality_state,
        profile_ready=_profile_for(db, dataset) is not None,
        validation_ready=latest_validation is not None,
        analysis_ready=dataset.status == DatasetStatus.ANALYSIS_READY.value,
        row_count=dataset.row_count,
        column_count=dataset.column_count,
        error_code=dataset.error_code,
        error_message=dataset.error_message,
        updated_at=dataset.updated_at,
    )


# --- dataset CRUD --------------------------------------------------------
@router.get("", response_model=Page[DatasetSummary])
def list_datasets(
    page: Pagination = Depends(pagination),
    status_filter: str | None = Query(default=None, alias="status"),
    source_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[DatasetSummary]:
    conditions = [Dataset.company_id == ctx.company_id, Dataset.deleted_at.is_(None)]
    if status_filter:
        conditions.append(Dataset.status == status_filter)
    if source_type:
        conditions.append(Dataset.source_type == source_type)
    if search:
        conditions.append(Dataset.name.ilike(f"%{search}%"))

    total = db.scalar(select(func.count()).select_from(Dataset).where(*conditions)) or 0
    rows = db.scalars(
        select(Dataset)
        .where(*conditions)
        .order_by(Dataset.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[DatasetSummary](
        items=[DatasetSummary.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> DatasetDetail:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    return to_detail(db, dataset)


@router.patch("/{dataset_id}", response_model=DatasetDetail)
def update_dataset(
    dataset_id: uuid.UUID,
    payload: DatasetUpdate,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> DatasetDetail:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    if payload.name is not None:
        dataset.name = payload.name
    if payload.description is not None:
        dataset.description = payload.description
    db.commit()
    db.refresh(dataset)
    return to_detail(db, dataset)


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Response:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    dataset_service.delete_dataset(db, dataset)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{dataset_id}/status", response_model=DatasetStatusRead)
def dataset_status(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> DatasetStatusRead:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    return build_status(db, dataset)


@router.get("/{dataset_id}/preview", response_model=PreviewRead)
def preview_dataset(
    dataset_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> PreviewRead:
    from app.analysis.duckdb_session import duckdb_connection, source_relation
    from app.db.models.enums import FileFormat
    from app.services.materialize import excel_to_parquet
    from app.services.storage_service import get_storage

    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    if not dataset.storage_key:
        raise NotReadyError("This dataset has no stored data yet.", code="DATA_NOT_READY")

    storage = get_storage()
    key = dataset.normalized_key or dataset.storage_key
    read_format = (
        FileFormat.PARQUET.value if dataset.normalized_key else dataset.file_format
    )

    temp = None
    try:
        with storage.as_local_file(key) as path:
            read_path = path
            if read_format == FileFormat.XLSX.value:
                from app.services.materialize import temp_path

                temp = temp_path(".parquet")
                excel_to_parquet(path, temp)
                read_path, read_format = temp, FileFormat.PARQUET.value

            with duckdb_connection() as conn:
                relation = source_relation(read_path, read_format)
                described = conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
                rows = conn.execute(
                    f"SELECT * FROM {relation} LIMIT {limit} OFFSET {offset}"
                ).fetchall()
        return PreviewRead(
            columns=[PreviewColumn(name=row[0], type=row[1]) for row in described],
            rows=[[None if value is None else str(value) for value in row] for row in rows],
            total_rows=dataset.row_count,
            limit=limit,
            offset=offset,
        )
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


# --- profile -------------------------------------------------------------
@router.get("/{dataset_id}/profile", response_model=ProfileEnvelope)
def get_profile(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> ProfileEnvelope:
    """Always 200. ``state`` tells the client whether to keep polling."""
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    profile = _profile_for(db, dataset)

    if profile is None:
        if dataset.status in {DatasetStatus.PROFILING.value, DatasetStatus.VALIDATING.value}:
            return ProfileEnvelope(state="running")
        if dataset.status in {
            DatasetStatus.PROFILING_FAILED.value,
            DatasetStatus.UPLOAD_FAILED.value,
        }:
            return ProfileEnvelope(state="failed", message=dataset.error_message)
        return ProfileEnvelope(state="pending")

    columns = db.scalars(
        select(ColumnProfile)
        .where(ColumnProfile.dataset_profile_id == profile.id)
        .order_by(ColumnProfile.ordinal_position)
    ).all()
    return ProfileEnvelope(
        state="ready",
        profile=DatasetProfileRead.model_validate(profile),
        columns=[ColumnProfileRead.model_validate(column) for column in columns],
    )


@router.get("/{dataset_id}/profile/columns", response_model=Page[ColumnProfileRead])
def get_profile_columns(
    dataset_id: uuid.UUID,
    page: Pagination = Depends(pagination),
    type_filter: str | None = Query(default=None, alias="type"),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[ColumnProfileRead]:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    profile = _profile_for(db, dataset)
    if profile is None:
        raise NotReadyError("This dataset has not been profiled yet.", code="PROFILE_NOT_READY")

    conditions = [ColumnProfile.dataset_profile_id == profile.id]
    if type_filter:
        conditions.append(ColumnProfile.inferred_type == type_filter)
    if search:
        conditions.append(ColumnProfile.column_name.ilike(f"%{search}%"))

    total = db.scalar(select(func.count()).select_from(ColumnProfile).where(*conditions)) or 0
    rows = db.scalars(
        select(ColumnProfile)
        .where(*conditions)
        .order_by(ColumnProfile.ordinal_position)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[ColumnProfileRead](
        items=[ColumnProfileRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/{dataset_id}/profile/regenerate", status_code=status.HTTP_202_ACCEPTED)
def regenerate_profile(
    dataset_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> dict[str, str]:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    if dataset.status in {DatasetStatus.PROFILING.value, DatasetStatus.VALIDATING.value}:
        raise ConflictError("Profiling is already running for this dataset.", code="JOB_ALREADY_RUNNING")
    if not dataset.storage_key:
        raise NotReadyError("This dataset has no stored data.", code="DATA_NOT_READY")

    enqueue_profiling(dataset.id, background_tasks)
    db.refresh(dataset)
    return {"dataset_id": str(dataset.id), "status": dataset.status}


# --- validation ----------------------------------------------------------
@router.get("/{dataset_id}/validation", response_model=SchemaValidationRead)
def get_validation(
    dataset_id: uuid.UUID,
    mode: str = Query(default=ValidationMode.STRUCTURAL.value),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> SchemaValidationRead:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    record = db.scalar(
        select(SchemaValidation)
        .where(SchemaValidation.dataset_id == dataset.id, SchemaValidation.mode == mode)
        .order_by(SchemaValidation.created_at.desc())
        .limit(1)
    )
    if record is None:
        raise NotFoundError("Validation has not run for this dataset yet.", code="VALIDATION_NOT_RUN")
    return SchemaValidationRead.model_validate(record)


@router.get("/{dataset_id}/validation/history", response_model=Page[SchemaValidationRead])
def validation_history(
    dataset_id: uuid.UUID,
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[SchemaValidationRead]:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    conditions = [SchemaValidation.dataset_id == dataset.id]
    total = db.scalar(select(func.count()).select_from(SchemaValidation).where(*conditions)) or 0
    rows = db.scalars(
        select(SchemaValidation)
        .where(*conditions)
        .order_by(SchemaValidation.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[SchemaValidationRead](
        items=[SchemaValidationRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


# --- KPI -----------------------------------------------------------------
def _candidate_read(candidate, column_lookup) -> KpiCandidateRead:  # noqa: ANN001
    column = column_lookup.get(candidate.column)
    datetime_stats = (column.datetime_stats or {}) if column else {}
    return KpiCandidateRead(
        column=candidate.column,
        score=candidate.score,
        reasons=candidate.reasons,
        suggested_aggregation=candidate.suggested_aggregation,
        dtype=column.inferred_type if column else None,
        cardinality=column.unique_count if column else None,
        detected_frequency=datetime_stats.get("detected_frequency"),
        min_date=datetime_stats.get("min_date"),
        max_date=datetime_stats.get("max_date"),
        distinct_periods=datetime_stats.get("distinct_periods"),
    )


@router.get("/{dataset_id}/kpi-candidates", response_model=KpiCandidatesRead)
def kpi_candidates(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> KpiCandidatesRead:
    """Recommend measures, time columns and dimensions (PRD section 11)."""
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    candidates, default, _ = kpi_service.get_candidates(db, dataset)

    profile = _profile_for(db, dataset)
    columns = (
        db.scalars(select(ColumnProfile).where(ColumnProfile.dataset_profile_id == profile.id)).all()
        if profile
        else []
    )
    lookup = {column.column_name: column for column in columns}

    return KpiCandidatesRead(
        measures=[_candidate_read(c, lookup) for c in candidates.measures],
        time_columns=[_candidate_read(c, lookup) for c in candidates.time_columns],
        dimensions=[_candidate_read(c, lookup) for c in candidates.dimensions],
        recommended_default=default,
    )


@router.post(
    "/{dataset_id}/kpi-definitions",
    response_model=KpiDefinitionEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def create_kpi_definition(
    dataset_id: uuid.UUID,
    payload: KpiDefinitionCreate,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> KpiDefinitionEnvelope:
    """Save the KPI selection and mark the dataset Analysis Ready."""
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    record, report = kpi_service.create_definition(
        db,
        dataset,
        {
            "name": payload.name,
            "column": payload.column,
            "aggregation": payload.aggregation.value,
            "time_column": payload.time_column,
            "dimensions": payload.dimensions,
            "comparison": payload.comparison.value,
            "comparison_config": payload.comparison_config,
            "filters": payload.filters,
        },
        company_id=ctx.company_id,
        user_id=ctx.user_id,
    )
    db.refresh(dataset)
    return KpiDefinitionEnvelope(
        kpi_definition=KpiDefinitionRead.model_validate(record),
        validation={
            "state": report.state,
            "issues": [issue.to_dict() for issue in report.issues],
        },
        analysis_ready=dataset.status == DatasetStatus.ANALYSIS_READY.value,
    )


@router.get("/{dataset_id}/kpi-definitions/active", response_model=KpiDefinitionRead)
def get_active_kpi(
    dataset_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> KpiDefinitionRead:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    return KpiDefinitionRead.model_validate(kpi_service.get_active_definition(db, dataset))


@router.get("/{dataset_id}/kpi-definitions", response_model=Page[KpiDefinitionRead])
def list_kpi_definitions(
    dataset_id: uuid.UUID,
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Page[KpiDefinitionRead]:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    conditions = [KpiDefinition.dataset_id == dataset.id]
    total = db.scalar(select(func.count()).select_from(KpiDefinition).where(*conditions)) or 0
    rows = db.scalars(
        select(KpiDefinition)
        .where(*conditions)
        .order_by(KpiDefinition.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page[KpiDefinitionRead](
        items=[KpiDefinitionRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.delete("/{dataset_id}/kpi-definitions/{kpi_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kpi_definition(
    dataset_id: uuid.UUID,
    kpi_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> Response:
    dataset = dataset_service.get_dataset(db, dataset_id, ctx.company_id)
    kpi_service.delete_definition(db, dataset, kpi_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
