from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.septa.filters import BadDate, BadModule, OpenDateFilter, normalize_module
from app.scrapers.septa.models import (
    EXCEL_COLUMNS,
    OPEN_BID_EXCEL_COLUMNS,
    SeptaBid,
    SeptaOpenBid,
)
from app.scrapers.septa.scraper import execute_run

router = APIRouter(prefix="/septa", tags=["septa"])


class ScrapeRequest(BaseModel):
    """What a run searches — which module, and an optional "opens from" date.

      module    — "quotes" (Open Quotes) or "open_bids" (the Bid module).
                  Defaults to quotes, so a caller that omits it gets the run it
                  got before the module was selectable. Exactly one is
                  searched; there is no "both".
      date_from — "opens from" date, YYYY-MM-DD. Optional with no default:
                  omitting it fetches every open row in the selected module,
                  which is the normal case. There is no "opens to" bound — the
                  filter is open-ended on purpose.

    Keyword, commodity-code and niche searching have been removed.
    """

    module: str | None = None
    date_from: str | None = None


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks, live_preview: bool = False) -> dict:
    dates = OpenDateFilter(opens_from=request.date_from)

    # Reject an unusable module or date here rather than in the worker: a 400 is
    # an immediate, correctable answer, whereas the scraper would start a
    # browser and either search the wrong module or quietly search everything
    # instead of the range asked for — both of which look like a working run.
    try:
        module = normalize_module(request.module)
    except BadModule as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{exc} — use 'quotes' for Open Quotes or 'open_bids' for the Bid module.",
        ) from exc

    try:
        dates.portal_value()
    except BadDate as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{exc} — use YYYY-MM-DD, e.g. 2026-08-05.",
        ) from exc

    search = dates.summary(module)

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
            "module": module,
            "date_from": dates.opens_from,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], dates.opens_from, module)
    return {
        "run_id": run["run_id"],
        "search": search,
        "module": module,
        "folder": run["folder"],
    }


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


def _open_bid_to_dict(bid: SeptaOpenBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in OPEN_BID_EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id)
    return data


@router.get("/open-bids")
def list_open_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search bid number / title"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return SEPTA Open Bids stored in the database, most recent first.

    A separate endpoint from `/bids` rather than a flag on it: the two grids
    have different columns, so one response shape could not describe both
    without nulls standing in for half of every row.
    """
    stmt = select(SeptaOpenBid).order_by(SeptaOpenBid.scraped_at.desc(), SeptaOpenBid.id.desc())
    if run_id:
        stmt = stmt.where(SeptaOpenBid.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                SeptaOpenBid.bid_number.ilike(like),
                SeptaOpenBid.title.ilike(like),
            )
        )
    try:
        rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — check DATABASE_URL in server/.env",
        ) from exc
    return {"open_bids": [_open_bid_to_dict(b) for b in rows], "count": len(rows)}
