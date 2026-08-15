"""Background job dispatch.

A deliberately thin indirection over FastAPI's ``BackgroundTasks``. Phase 1 has
exactly one job type and a single node, so a broker would be operational weight
for no benefit - but swapping this for Celery later touches only this file.
"""

import uuid

from fastapi import BackgroundTasks

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import session_scope

logger = get_logger(__name__)


def _run_pipeline(dataset_id: uuid.UUID) -> None:
    # Imported here to avoid a circular import at module load.
    from app.services.profiling_service import run_pipeline

    with session_scope() as db:
        run_pipeline(db, dataset_id)


def enqueue_profiling(dataset_id: uuid.UUID, background_tasks: BackgroundTasks | None = None) -> None:
    """Queue validation + profiling for a dataset.

    ``PROFILING_ASYNC=false`` runs it inline, which is what the test suite uses
    so assertions do not race the pipeline.
    """
    if not settings.profiling_async or background_tasks is None:
        _run_pipeline(dataset_id)
        return
    background_tasks.add_task(_run_pipeline, dataset_id)
