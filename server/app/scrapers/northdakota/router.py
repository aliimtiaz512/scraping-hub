from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.northdakota.models import EXCEL_COLUMNS, NorthDakotaBid
from app.scrapers.northdakota.scraper import execute_run

router = APIRouter(prefix="/northdakota", tags=["northdakota"])


class ScrapeRequest(BaseModel):
    # Both optional; an empty search returns every current public solicitation.
    keyword: str = ""
    commodity: str = ""


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks, live_preview: bool = False) -> dict:
    keyword = request.keyword.strip()
    commodity = request.commodity.strip()
    search = ", ".join(
        part for part in (
            f"keyword={keyword}" if keyword else "",
            f"commodity={commodity}" if commodity else "",
        ) if part
    ) or "all public solicitations"

    label = timestamp()  # e.g. 2026-07-18 14-30-05
    folder = run_manager.make_run_folder(f"Document_Bids_NorthDakota ({label})")
    run = run_manager.create_run(
        "northdakota",
        folder,
        {
            "label": label,
            "search": search,
            "keyword": keyword,
            "commodity": commodity,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], keyword, commodity)
    return {"run_id": run["run_id"], "search": search, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="northdakota")}


def _bid_to_dict(bid: NorthDakotaBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search RFx name / commodity / status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return North Dakota solicitations stored in the database, most recent first."""
    stmt = select(NorthDakotaBid).order_by(NorthDakotaBid.scraped_at.desc(), NorthDakotaBid.id.desc())
    if run_id:
        stmt = stmt.where(NorthDakotaBid.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                NorthDakotaBid.title.ilike(like),
                NorthDakotaBid.commodity.ilike(like),
                NorthDakotaBid.status.ilike(like),
                NorthDakotaBid.rfp_id.ilike(like),
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
