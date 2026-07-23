from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.unison import runner
from app.scrapers.unison.models import EXCEL_COLUMNS, UnisonRequest

router = APIRouter(prefix="/unison", tags=["unison"])


class ScrapeRequest(BaseModel):
    # Optional dashboard filter passed straight to the engine's run_scraper.
    filter_by: str | None = None


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks) -> dict:
    filter_by = (request.filter_by or "").strip() or None
    search = f"filter={filter_by}" if filter_by else "all requests"

    date_folder = f"Unison_{timestamp('%Y-%m-%d')}"
    folder = run_manager.make_run_folder(date_folder)
    run = run_manager.create_run(
        "unison",
        folder,
        {"search": search, "filter_by": filter_by, "excel_exported": False},
    )
    background_tasks.add_task(runner.execute_run, run["run_id"], filter_by)
    return {"run_id": run["run_id"], "search": search, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="unison")}


def _row_to_dict(row: UnisonRequest) -> dict:
    data = {attr: getattr(row, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=row.id, run_id=row.run_id)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search buyer / description / number"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return Unison buyer requests stored in the database, most recent first."""
    stmt = select(UnisonRequest).order_by(UnisonRequest.scraped_at.desc(), UnisonRequest.id.desc())
    if run_id:
        stmt = stmt.where(UnisonRequest.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                UnisonRequest.buyer.ilike(like),
                UnisonRequest.buyer_description.ilike(like),
                UnisonRequest.buyer_number.ilike(like),
            )
        )
    try:
        rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="Database unavailable — check DATABASE_URL in server/.env"
        ) from exc
    return {"bids": [_row_to_dict(r) for r in rows], "count": len(rows)}
