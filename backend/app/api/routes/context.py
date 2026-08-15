"""Acting company/user.

Replaces the PRD tree's ``auth.py``: Phase 1 has no authentication, so there is
no login endpoint. This exposes the seeded context the frontend attributes work
to, and reports ``authenticated: false`` so the UI renders no account controls.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, get_current_context, get_db
from app.db.models import Company, User
from app.schemas.common import ContextRead

router = APIRouter(tags=["context"])


@router.get("/context", response_model=ContextRead)
def read_context(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_current_context),
) -> ContextRead:
    company = db.get(Company, ctx.company_id)
    user = db.get(User, ctx.user_id) if ctx.user_id else None
    return ContextRead(
        company_id=str(ctx.company_id),
        company_name=company.name if company else "Unknown",
        user_id=str(ctx.user_id) if ctx.user_id else "",
        user_name=user.display_name if user else "Unknown",
        user_email=user.email if user else "",
        authenticated=False,
    )
