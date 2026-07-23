import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.bidnet import export
from app.scrapers.bidnet.keywords import get_niche_catalog
from app.scrapers.bidnet.models import EXCEL_COLUMNS, BidnetBid
from app.scrapers.bidnet.scraper import execute_run

router = APIRouter(prefix="/bidnet", tags=["bidnet"])


class ScrapeRequest(BaseModel):
    # One or more keywords; each is searched separately in the same run.
    keywords: list[str]


@router.get("/keywords")
def keywords() -> dict:
    """Return the curated keyword catalog, organized by niche and tier."""
    return {"niches": get_niche_catalog()}


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks) -> dict:
    # Strip, drop blanks, de-duplicate while preserving order.
    keywords = list(dict.fromkeys(kw.strip() for kw in request.keywords if kw.strip()))
    if not keywords:
        raise HTTPException(status_code=400, detail="at least one keyword is required")
    label = timestamp()  # e.g. 2026-07-08 14-30-05
    # Per-run workspace parent (its name becomes the run's ZIP name), inside
    # which results are foldered per niche+tier (Bidnetdirect_AI-ML_core, ...) —
    # the scraper builds those, keeping niches separated. Timestamped so
    # concurrent runs never share a workspace.
    folder = run_manager.make_run_folder(f"Bidnetdirect ({label})")
    run = run_manager.create_run(
        "bidnet",
        folder,
        {
            "label": label,
            "keyword": ", ".join(keywords),
            "keywords": keywords,
            "excel_exported": False,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], keywords)
    return {"run_id": run["run_id"], "keywords": keywords, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="bidnet")}


def _bid_to_dict(bid: BidnetBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search title / solicitation / reference"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return BidNet solicitations stored in the database, most recent first."""
    stmt = select(BidnetBid).order_by(BidnetBid.scraped_at.desc(), BidnetBid.id.desc())
    if run_id:
        stmt = stmt.where(BidnetBid.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                BidnetBid.title.ilike(like),
                BidnetBid.solicitation_number.ilike(like),
                BidnetBid.reference_number.ilike(like),
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


@router.get("/export")
def export_excel() -> FileResponse:
    """On-demand Excel of every stored solicitation (the export button).

    Built into a temp file and deleted after the response streams — nothing is
    written to local storage."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    out_path = Path(tmp.name)
    try:
        export.export_all_excel(out_path)
    except OperationalError as exc:
        out_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — check DATABASE_URL in server/.env",
        ) from exc
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path=str(out_path),
        filename="bids_export.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(out_path.unlink, missing_ok=True),
    )
