"""Aggregates every route module under a single router."""

from fastapi import APIRouter

from app.api.routes import context, datasets, rca, sql_connections, sql_editor, uploads

api_router = APIRouter()
api_router.include_router(context.router)
api_router.include_router(uploads.router)
api_router.include_router(datasets.router)
api_router.include_router(sql_connections.router)
api_router.include_router(sql_editor.router)
api_router.include_router(rca.router)
