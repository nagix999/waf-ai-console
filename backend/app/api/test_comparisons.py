"""No execution, prompt activation, decryption, or mutation in comparison GET."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import TestRun
from ..security import Principal, require_scope
from ..services.test_comparisons import compare_test_runs
from ..services.test_runs import read_snapshot
from ..test_comparison_schemas import TestComparisonResponse

router = APIRouter(prefix="/test-runs", tags=["test-runs"])
Admin = Annotated[Principal, Depends(require_scope("admin"))]
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{candidate_id}/comparison", response_model=TestComparisonResponse)
def get_test_comparison(candidate_id: UUID, baseline_id: UUID, db: DbSession, _principal: Admin,
                        limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
                        changes_only: bool = False):
    if candidate_id == baseline_id:
        raise HTTPException(422, "test_comparison_same_run")
    read_snapshot(db)
    candidate = db.get(TestRun, str(candidate_id))
    baseline = db.get(TestRun, str(baseline_id))
    if candidate is None or baseline is None:
        raise HTTPException(404, "test_run_not_found")
    return compare_test_runs(db, baseline, candidate, limit=limit, offset=offset, changes_only=changes_only)
