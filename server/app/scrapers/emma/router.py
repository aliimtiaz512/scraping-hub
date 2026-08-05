"""EMMA (eMaryland Marketplace Advantage) API.

Signs in, opens Public Solicitations, optionally applies the filter bar
(Main Category / Solicitation Type / Status), scrapes the whole results grid,
stores every kept solicitation, and rebuilds the run's Excel from the DB. Mirrors
the North Dakota router.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.emma.models import EXCEL_COLUMNS, EmmaBid
from app.scrapers.emma.scraper import execute_run

router = APIRouter(prefix="/emma", tags=["emma"])


class ScrapeRequest(BaseModel):
    # All optional; an empty request captures every current public solicitation.
    # These are the three filters the portal shows above the results grid.
    keyword: str = ""
    status: str = ""
    category: str = ""


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks, live_preview: bool = False) -> dict:
    keyword = request.keyword.strip()
    status = request.status.strip()
    category = request.category.strip()
    search = ", ".join(
        part for part in (
            f"keyword={keyword}" if keyword else "",
            f"status={status}" if status else "",
            f"category={category}" if category else "",
        ) if part
    ) or "all public solicitations"

    label = timestamp()  # e.g. 2026-07-30 14-30-05
    folder = run_manager.make_run_folder(f"Document_Bids_EMMA ({label})")
    run = run_manager.create_run(
        "emma",
        folder,
        {
            "label": label,
            "search": search,
            "keyword": keyword,
            "status": status,
            "category": category,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], keyword, status, category)
    return {"run_id": run["run_id"], "search": search, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="emma")}


def _bid_to_dict(bid: EmmaBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id, emma_id=bid.emma_id, documents=bid.documents or [])
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search title / category / status / ID"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return EMMA solicitations stored in the database, most recent first."""
    stmt = select(EmmaBid).order_by(EmmaBid.scraped_at.desc(), EmmaBid.id.desc())
    if run_id:
        stmt = stmt.where(EmmaBid.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                EmmaBid.title.ilike(like),
                EmmaBid.main_category.ilike(like),
                EmmaBid.status.ilike(like),
                EmmaBid.bpm_code.ilike(like),
            )
        )
    try:
        rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — check DATABASE_URL in server/.env",
        ) from exc
    return {"bids": [_bid_to_dict(b) for b in rows], "count": len(rows)}
