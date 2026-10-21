"""EMMA (eMaryland Marketplace Advantage) API.

Signs in, opens Public Solicitations, optionally applies the filter bar
(Main Category / Solicitation Type / Status), scrapes the whole results grid,
stores every kept solicitation, and rebuilds the run's Excel from the DB. Mirrors
the North Dakota router.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import jobs, run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.emma.models import EXCEL_COLUMNS, EmmaBid
from app.scrapers.emma.scraper import describe_window, execute_run
from app.scrapers.myflorida.dates import PostingDateRange

router = APIRouter(prefix="/emma", tags=["emma"])

# What a date in the request may look like: ISO from the console's date pickers,
# or the portal's own m/d/yyyy for someone calling the API by hand.
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")


class ScrapeRequest(BaseModel):
    # All optional; an empty request captures every current public solicitation.
    # keyword / status / category are the three filters the portal shows above
    # the results grid. date_from / date_to bound the Publish Date and are
    # applied on our side; either may be left blank.
    keyword: str = ""
    status: str = ""
    category: str = ""
    date_from: str = ""
    date_to: str = ""


def _parse_date(value: str, label: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise HTTPException(
        status_code=400,
        detail=f"{label} date is not a date I can read: {text!r}. Use yyyy-mm-dd (e.g. 2026-10-01).",
    )


def parse_publish_window(date_from: str, date_to: str) -> PostingDateRange:
    """The requested Publish Date range, or a 400 the console can show as-is.

    An inverted range is rejected rather than swapped: it would discard every
    bid, and an empty run is the outcome most easily mistaken for "no bids".
    """
    window = PostingDateRange(_parse_date(date_from, "From"), _parse_date(date_to, "To"))
    if window.start and window.end and window.end < window.start:
        raise HTTPException(
            status_code=400,
            detail=f"To date ({window.end.isoformat()}) is before the From date "
            f"({window.start.isoformat()}) — no bid can be published in that range.",
        )
    return window


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, live_preview: bool = False) -> dict:
    keyword = request.keyword.strip()
    status = request.status.strip()
    category = request.category.strip()
    window = parse_publish_window(request.date_from, request.date_to)
    date_from, date_to = window.isoformat()
    search = ", ".join(
        part for part in (
            f"keyword={keyword}" if keyword else "",
            f"status={status}" if status else "",
            f"category={category}" if category else "",
            describe_window(window),
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
            "date_from": date_from,
            "date_to": date_to,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    jobs.submit(run["run_id"], execute_run, run["run_id"], keyword, status, category, window)
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
