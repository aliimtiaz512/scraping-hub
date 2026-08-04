from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.septa.filters import BadDate, OpenDateRange
from app.scrapers.septa.models import EXCEL_COLUMNS, SeptaBid
from app.scrapers.septa.scraper import execute_run

router = APIRouter(prefix="/septa", tags=["septa"])


class ScrapeRequest(BaseModel):
    """What a run searches — an optional Open Date Range, and nothing else.

    Both ends are optional and there is no default. A request with neither
    fetches every open quote the portal is showing; that is the normal case.
    Keyword, commodity-code and niche searching have been removed.
      date_from — "opens from" date, YYYY-MM-DD
      date_to   — "opens to" date, YYYY-MM-DD
    """

    date_from: str | None = None
    date_to: str | None = None


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks, live_preview: bool = False) -> dict:
    dates = OpenDateRange(start=request.date_from, end=request.date_to)

    # Reject an unparseable date here rather than in the worker: a 400 is an
    # immediate, correctable answer, whereas the scraper would start a browser,
    # warn, and quietly search everything instead of the range that was asked
    # for — which looks like the filter being ignored.
    try:
        dates.portal_values()
    except BadDate as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{exc} — use YYYY-MM-DD, e.g. 2026-08-05.",
        ) from exc

    search = dates.summary()

    label = timestamp()  # e.g. 2026-07-20 14-30-05
    # Per-run workspace folder. Timestamped so concurrent runs never share a
    # workspace — each is packaged and deleted independently on completion.
    # A SEPTA run's only output is the spreadsheet, so nothing else lands here.
    folder = run_manager.make_run_folder(f"Septa ({label})")
    run = run_manager.create_run(
        "septa",
        folder,
        {
            "label": label,
            "search": search,
            "date_from": dates.start,
            "date_to": dates.end,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], dates.start, dates.end)
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
