from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.db import get_session
from app.scrapers.myflorida.commodity_codes import CATEGORIES, PRIORITY_LEVELS, get_codes
from app.scrapers.myflorida.models import Bid
from app.scrapers.myflorida.scraper import execute_run

router = APIRouter(prefix="/myflorida", tags=["myflorida"])


class ScrapeRequest(BaseModel):
    category: str
    priority: str = "high"  # high | high_medium | all


@router.get("/categories")
def categories() -> dict:
    return {
        "categories": [
            {"key": key, "label": value["label"], "codes": value["codes"]}
            for key, value in CATEGORIES.items()
        ],
        "priority_levels": list(PRIORITY_LEVELS.keys()),
    }


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks) -> dict:
    if request.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category: {request.category}")
    if request.priority not in PRIORITY_LEVELS:
        raise HTTPException(status_code=400, detail=f"Unknown priority level: {request.priority}")

    codes = get_codes(request.category, request.priority)
    folder = run_manager.make_run_folder(f"run_{datetime.now():%Y-%m-%d_%H-%M-%S}")
    run = run_manager.create_run(
        "myflorida",
        folder,
        {
            "category": request.category,
            "category_label": CATEGORIES[request.category]["label"],
            "priority": request.priority,
            "codes": codes,
            "excel_exported": False,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], codes)
    return {"run_id": run["run_id"], "codes": codes, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="myflorida")}


def _bid_to_dict(bid: Bid) -> dict:
    return {
        "id": bid.id,
        "run_id": bid.run_id,
        "category": bid.category,
        "ad_number": bid.ad_number,
        "title": bid.title,
        "agency": bid.agency,
        "ad_type": bid.ad_type,
        "status": bid.status,
        "commodity_codes": bid.commodity_codes,
        "ad_date": bid.ad_date,
        "open_date": bid.open_date,
        "close_date": bid.close_date,
        "estimated_amount": float(bid.estimated_amount) if bid.estimated_amount is not None else None,
        "raw_data": bid.raw_data,
    }


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    category: str | None = Query(None, description="Filter by category key"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return bids stored in the database, most recent first."""
    stmt = select(Bid).order_by(Bid.scraped_at.desc(), Bid.id.desc())
    if run_id:
        stmt = stmt.where(Bid.run_id == run_id)
    if category:
        stmt = stmt.where(Bid.category == category)
    try:
        rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — check DATABASE_URL in server/.env",
        ) from exc
    return {"bids": [_bid_to_dict(b) for b in rows], "count": len(rows)}
