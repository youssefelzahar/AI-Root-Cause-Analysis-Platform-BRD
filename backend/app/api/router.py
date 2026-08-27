"""Aggregates every route module under a single router."""

from fastapi import APIRouter

from app.api.routes import (
    ai,
    anomalies,
    context,
    datasets,
    investigations,
    rca,
    sql_connections,
    sql_editor,
    uploads,
)

api_router = APIRouter()
api_router.include_router(context.router)
api_router.include_router(uploads.router)
api_router.include_router(datasets.router)
api_router.include_router(sql_connections.router)
api_router.include_router(sql_editor.router)
api_router.include_router(rca.router)
api_router.include_router(anomalies.router)
api_router.include_router(investigations.router)
api_router.include_router(investigations.evidence_router)
api_router.include_router(ai.router)
