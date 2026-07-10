from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.wisconsin.models import EXCEL_COLUMNS, WisconsinBid
from app.scrapers.wisconsin.scraper import execute_run

router = APIRouter(prefix="/wisconsin", tags=["wisconsin"])


class ScrapeRequest(BaseModel):
    # All optional; an empty search returns every current solicitation.
    keyword: str = ""
    agency: str = ""
    nigp_code: str = ""


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks) -> dict:
    keyword = request.keyword.strip()
    agency = request.agency.strip()
    nigp_code = request.nigp_code.strip()
    search = ", ".join(
        part for part in (
            f"keyword={keyword}" if keyword else "",
            f"agency={agency}" if agency else "",
            f"nigp={nigp_code}" if nigp_code else "",
        ) if part
    ) or "all current solicitations"

    label = timestamp()  # e.g. 2026-07-08 14-30-05
    folder = run_manager.make_run_folder(f"Document_Bids_Wisconsin ({label})")
    run = run_manager.create_run(
        "wisconsin",
        folder,
        {
            "label": label,
            "search": search,
            "keyword": keyword,
            "agency": agency,
            "nigp_code": nigp_code,
            "excel_exported": False,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], keyword, agency, nigp_code)
    return {"run_id": run["run_id"], "search": search, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="wisconsin")}


def _bid_to_dict(bid: WisconsinBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search title / reference / agency"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return Wisconsin solicitations stored in the database, most recent first."""
    stmt = select(WisconsinBid).order_by(WisconsinBid.scraped_at.desc(), WisconsinBid.id.desc())
    if run_id:
        stmt = stmt.where(WisconsinBid.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                WisconsinBid.event_title.ilike(like),
                WisconsinBid.solicitation_reference.ilike(like),
                WisconsinBid.agency.ilike(like),
                WisconsinBid.event_number.ilike(like),
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
