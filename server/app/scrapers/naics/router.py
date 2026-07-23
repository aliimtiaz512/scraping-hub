"""NAICS reference tool routes — list, search, and a refresh scrape."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.db import get_session
from app.scrapers.naics import runner
from app.scrapers.naics.models import NaicsCode

router = APIRouter(prefix="/naics", tags=["naics"])


@router.get("")
def list_naics(
    q: str = Query("", description="Search code or title"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    """List NAICS codes (optionally filtered by `q`), paginated and code-ordered."""
    offset = (page - 1) * limit
    base = select(NaicsCode)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(or_(func.lower(NaicsCode.code).like(like), func.lower(NaicsCode.title).like(like)))
    try:
        total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        rows = session.execute(
            base.order_by(NaicsCode.code).offset(offset).limit(limit)
        ).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="Database unavailable — check DATABASE_URL in server/.env"
        ) from exc
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "results": [{"code": r.code, "title": r.title} for r in rows],
    }


@router.get("/search")
def search_naics(
    q: str = Query("", description="Search code or title"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    """Alias of the list endpoint with a query — kept for API parity."""
    return list_naics(q=q, page=page, limit=limit, session=session)


@router.post("/scrape")
def start_scrape(background_tasks: BackgroundTasks) -> dict:
    """Refresh the NAICS reference table from the source index page."""
    folder = run_manager.make_run_folder("Naics")
    run = run_manager.create_run("naics", folder, {"search": "NAICS reference refresh"})
    background_tasks.add_task(runner.execute_run, run["run_id"])
    return {"run_id": run["run_id"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="naics")}
