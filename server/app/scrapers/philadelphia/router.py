"""City of Philadelphia (PHLContracts) API.

Signs in to the vendor portal, opens the Open Bids list in full, reads every
bid's summary row and detail page, saves each bid's attachments into its own
folder, and packages the lot into one ZIP with a master summary sheet at its
root. Bids are upserted into `city_of_philadelphia_bids` by bid number, so a
second run updates what it already holds rather than duplicating it.

A run defaults to every open bid: "the open bids" is the whole scope the portal
publishes. Posting filters instead runs the portal's own Advanced Search — the
same pipeline over a narrower set of bids, not a different one.
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import jobs, run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.philadelphia import search
from app.scrapers.philadelphia.models import EXCEL_COLUMNS, PhiladelphiaBid
from app.scrapers.philadelphia.scraper import execute_run

router = APIRouter(prefix="/philadelphia", tags=["philadelphia"])


@router.get("/search/fields")
def search_fields() -> dict:
    """Which criteria a run accepts, and what each is called on the portal.

    Served rather than duplicated in the dashboard so the two cannot drift: the
    form a user fills in is built from the same list the scraper fills in.
    """
    return {
        "text": [{"key": k, "label": search.LABELS[k]} for k in search.TEXT_FIELDS],
        "select": [{"key": k, "label": search.LABELS[k]} for k in search.SELECT_FIELDS],
    }


@router.post("/scrape")
def start_scrape(
    live_preview: bool = False,
    filters: dict | None = Body(
        None,
        description=(
            "Advanced Search criteria. Omit or send {} to take every open bid. "
            "Dropdown values may be the portal's code or the text it shows."
        ),
    ),
) -> dict:
    """Start a run. Returns at once with the run id.

    With no filters this walks the whole Open Bids list, as it always has. With
    filters it drives the portal's Advanced Search and takes what that returns —
    and unknown or blank criteria are dropped here rather than carried to the
    browser, so what the run records as its search is what it actually ran.
    """
    criteria = search.clean_filters(filters)
    label = timestamp()  # e.g. 2026-08-14 14-30-05
    folder = run_manager.make_run_folder(f"Document_Bids_Philadelphia ({label})")
    run = run_manager.create_run(
        "philadelphia",
        folder,
        {
            "label": label,
            "search": search.describe(criteria),
            "filters": criteria,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    jobs.submit(run["run_id"], execute_run, run["run_id"])
    return {
        "run_id": run["run_id"],
        "search": run["search"],
        "folder": run["folder"],
        "filters": criteria,
    }


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="philadelphia")}


def _bid_to_dict(bid: PhiladelphiaBid) -> dict:
    data = {attr: getattr(bid, attr, None) for attr, _ in EXCEL_COLUMNS if attr != "folder"}
    data.update(
        run_id=bid.run_id,
        detail_url=bid.detail_url,
        file_paths=bid.file_paths or [],
        extra_header_data=bid.extra_header_data or {},
        first_seen_at=bid.first_seen_at,
        scraped_at=bid.scraped_at,
    )
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search bid number / description / buyer"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Stored Philadelphia bids, most recently scraped first."""
    stmt = select(PhiladelphiaBid).order_by(
        PhiladelphiaBid.scraped_at.desc(), PhiladelphiaBid.bid_number.desc()
    )
    if run_id:
        stmt = stmt.where(PhiladelphiaBid.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                PhiladelphiaBid.bid_number.ilike(like),
                PhiladelphiaBid.description.ilike(like),
                PhiladelphiaBid.buyer.ilike(like),
                PhiladelphiaBid.organization.ilike(like),
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
