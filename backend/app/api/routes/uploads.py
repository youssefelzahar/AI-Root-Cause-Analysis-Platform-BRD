"""Upload endpoints (PRD section 6)."""

from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, get_current_context, get_db
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.schemas.dataset import DatasetDetail, DatasetStatusRead, UploadResponse
from app.services import dataset_service
from app.services.jobs import enqueue_profiling

router = APIRouter(prefix="/uploads", tags=["uploads"])
logger = get_logger(__name__)


async def _stream_upload_file(upload: UploadFile) -> AsyncIterator[bytes]:
    """Yield the file in bounded 1 MB chunks (PRD section 6)."""
    while True:
        chunk = await upload.read(settings.upload_chunk_bytes)
        if not chunk:
            break
        yield chunk


async def _stream_request_body(request: Request) -> AsyncIterator[bytes]:
    async for chunk in request.stream():
        if chunk:
            yield chunk


def _finalise(
    db: Session,
    dataset,  # noqa: ANN001
    ctx: RequestContext,
    background_tasks: BackgroundTasks,
) -> UploadResponse:
    duplicate = (
        dataset_service.find_duplicate(db, ctx.company_id, dataset.checksum_sha256, dataset.id)
        if dataset.checksum_sha256
        else None
    )
    if duplicate is not None and settings.reject_duplicate_uploads:
        dataset_service.delete_dataset(db, dataset)
        raise ConflictError(
            "An identical file has already been uploaded.",
            code="DUPLICATE_UPLOAD",
            details={"existing_dataset_id": str(duplicate.id)},
        )

    enqueue_profiling(dataset.id, background_tasks)
    db.refresh(dataset)
    return UploadResponse(
        dataset=_to_detail(db, dataset), duplicate_of=duplicate.id if duplicate else None
    )


def _to_detail(db: Session, dataset) -> DatasetDetail:  # noqa: ANN001
    from app.api.routes.datasets import to_detail

    return to_detail(db, dataset)


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> UploadResponse:
    """Upload a CSV or Excel file.

    The size cap is enforced by ``MaxBodySizeMiddleware`` before the body is
    buffered, and again while streaming to storage.
    """
    original = dataset_service.sanitize_filename(file.filename)
    file_format, extension = dataset_service.resolve_format(original)

    dataset = dataset_service.create_dataset_row(
        db,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        name=name or original.rsplit(".", 1)[0] or original,
        original_filename=original,
        file_format=file_format,
        description=description,
    )
    dataset = await dataset_service.store_upload(db, dataset, _stream_upload_file(file), extension)
    return _finalise(db, dataset, ctx, background_tasks)


@router.post("/stream", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_stream(
    request: Request,
    background_tasks: BackgroundTasks,
    filename: str = Query(..., description="Original filename, used for metadata and format detection"),
    name: str | None = Query(default=None),
    description: str | None = Query(default=None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> UploadResponse:
    """Raw-body upload.

    Avoids Starlette's multipart spool entirely, so the bytes go straight from
    the socket to storage. Preferred for very large files.
    """
    original = dataset_service.sanitize_filename(filename)
    file_format, extension = dataset_service.resolve_format(original)

    dataset = dataset_service.create_dataset_row(
        db,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        name=name or original.rsplit(".", 1)[0] or original,
        original_filename=original,
        file_format=file_format,
        description=description,
    )
    dataset = await dataset_service.store_upload(db, dataset, _stream_request_body(request), extension)
    return _finalise(db, dataset, ctx, background_tasks)


@router.get("/{dataset_id}/status", response_model=DatasetStatusRead)
def upload_status(
    dataset_id: str,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> DatasetStatusRead:
    """Small payload for the upload screen's polling loop."""
    import uuid as _uuid

    from app.api.routes.datasets import build_status

    dataset = dataset_service.get_dataset(db, _uuid.UUID(dataset_id), ctx.company_id)
    return build_status(db, dataset)
