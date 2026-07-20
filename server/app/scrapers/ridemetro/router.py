from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.ridemetro.models import EXCEL_COLUMNS, RideMetroBid
from app.scrapers.ridemetro.scraper import execute_run

router = APIRouter(prefix="/ridemetro", tags=["ridemetro"])


@router.post("/scrape")
def start_scrape(background_tasks: BackgroundTasks) -> dict:
    label = timestamp()  # e.g. 2026-07-08 14-30-05
    # Date-bucketed storage (mirrors SEPTA/MyFlorida): every run on the same
    # calendar day drops its Excel sheet into one shared RideMetro-<date> folder;
    # the next day gets a fresh folder. RideMetro is list-only (no document
    # downloads), so the folder holds only the generated sheets — one per run.
    date_folder = f"RideMetro-{timestamp('%Y-%m-%d')}"
    folder = run_manager.make_run_folder(date_folder)
    run = run_manager.create_run("ridemetro", folder, {"label": label})
    background_tasks.add_task(execute_run, run["run_id"])
    return {"run_id": run["run_id"], "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="ridemetro")}


def _bid_to_dict(bid: RideMetroBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id, raw_data=bid.raw_data)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return RideMetro opportunities stored in the database, most recent first."""
    stmt = select(RideMetroBid).order_by(RideMetroBid.scraped_at.desc(), RideMetroBid.id.desc())
    if run_id:
        stmt = stmt.where(RideMetroBid.run_id == run_id)
    try:
        rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — check DATABASE_URL in server/.env",
        ) from exc
    return {"bids": [_bid_to_dict(b) for b in rows], "count": len(rows)}
