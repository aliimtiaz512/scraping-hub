from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.septa.models import EXCEL_COLUMNS, SeptaBid
from app.scrapers.septa.scraper import execute_run

router = APIRouter(prefix="/septa", tags=["septa"])


class ScrapeRequest(BaseModel):
    # All three filters are optional and freely combinable. A blank request means
    # today's open quotes (the portal's own default).
    #   date_filter    — YYYY-MM-DD "opens on" date
    #   keyword        — free-text keyword search
    #   commodity_code — SEPTA commodity code
    date_filter: str | None = None
    keyword: str | None = None
    commodity_code: str | None = None


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks) -> dict:
    date_filter = (request.date_filter or "").strip() or None
    keyword = (request.keyword or "").strip() or None
    commodity_code = (request.commodity_code or "").strip() or None

    search = ", ".join(
        part for part in (
            f"date={date_filter}" if date_filter else "",
            f"keyword={keyword}" if keyword else "",
            f"commodity={commodity_code}" if commodity_code else "",
        ) if part
    ) or "today's open quotes"

    label = timestamp()  # e.g. 2026-07-20 14-30-05
    # Date-bucketed storage (mirrors MyFlorida): every run on the same calendar
    # day drops its Excel sheet into one shared Septa-<date> folder; the next day
    # gets a fresh folder. SEPTA has no document downloads, so the folder holds
    # only the generated sheets — one per run.
    date_folder = f"Septa-{timestamp('%Y-%m-%d')}"
    folder = run_manager.make_run_folder(date_folder)
    run = run_manager.create_run(
        "septa",
        folder,
        {
            "label": label,
            "search": search,
            "date_filter": date_filter,
            "keyword": keyword,
            "commodity_code": commodity_code,
            "excel_exported": False,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], date_filter, keyword, commodity_code)
    return {"run_id": run["run_id"], "search": search, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="septa")}


def _bid_to_dict(bid: SeptaBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search requisition number / summary"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return SEPTA quotes stored in the database, most recent first."""
    stmt = select(SeptaBid).order_by(SeptaBid.scraped_at.desc(), SeptaBid.id.desc())
    if run_id:
        stmt = stmt.where(SeptaBid.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                SeptaBid.requisition_number.ilike(like),
                SeptaBid.summary.ilike(like),
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
