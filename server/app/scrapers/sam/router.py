from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.sam import runner
from app.scrapers.sam.evaluation import evaluate
from app.scrapers.sam.models import EXCEL_COLUMNS, SamBid

router = APIRouter(prefix="/sam", tags=["sam"])


class ScrapeRequest(BaseModel):
    # All optional. date_filter is the from-date; date_to defaults to today.
    date_filter: str | None = None          # YYYY-MM-DD (start of updated-date range)
    date_to: str | None = None              # YYYY-MM-DD (end of range)
    naics_codes: list[str] | None = None    # 6-digit NAICS codes to filter
    award_notice: bool = False              # include Award Notice type


class EvaluateRequest(BaseModel):
    bid_id: str
    full_text: str
    naics_code: str | None = None
    title: str | None = None


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks, live_preview: bool = False) -> dict:
    date_from = (request.date_filter or "").strip() or None
    date_to = (request.date_to or "").strip() or None
    naics_codes = [c.strip() for c in (request.naics_codes or []) if c.strip()]

    search = ", ".join(
        part for part in (
            f"from={date_from}" if date_from else "",
            f"to={date_to}" if date_to else "",
            f"naics={'/'.join(naics_codes)}" if naics_codes else "",
            "award_notice" if request.award_notice else "",
        ) if part
    ) or "all active solicitations"

    # Per-run workspace folder (its name becomes the run's ZIP name). Timestamped
    # so concurrent runs never share a workspace — each is zipped and deleted
    # independently on completion.
    folder = run_manager.make_run_folder(f"SAM ({timestamp()})")
    run = run_manager.create_run(
        "sam",
        folder,
        {
            "search": search,
            "date_from": date_from,
            "date_to": date_to,
            "naics_codes": naics_codes,
            "award_notice": request.award_notice,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    background_tasks.add_task(
        runner.execute_run,
        run["run_id"],
        date_from,
        date_to,
        naics_codes,
        request.award_notice,
        not live_preview,  # headless unless this is a live-preview run
    )
    return {"run_id": run["run_id"], "search": search, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="sam")}


@router.post("/scrape/stop/{run_id}")
def stop_scrape(run_id: str) -> dict:
    if not runner.request_stop(run_id):
        raise HTTPException(status_code=404, detail=f"No active SAM run: {run_id}")
    return {"success": True, "message": "Stop signal sent — finishing the current bid then saving."}


@router.get("/screenshot/{run_id}")
def get_screenshot(run_id: str) -> dict:
    scraper = runner.get_live_scraper(run_id)
    if scraper is None:
        raise HTTPException(status_code=404, detail="Run not found or scraper not active")
    b64 = scraper.get_screenshot_base64()
    if not b64:
        raise HTTPException(status_code=500, detail="Could not capture screenshot")
    return {"screenshot": b64}


def _bid_to_dict(bid: SamBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search title / notice id / NAICS"),
    decision: str | None = Query(None, description="Filter by decision"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return SAM bids stored in the database, most recent first."""
    stmt = select(SamBid).order_by(SamBid.scraped_at.desc(), SamBid.id.desc())
    if run_id:
        stmt = stmt.where(SamBid.run_id == run_id)
    if decision:
        stmt = stmt.where(SamBid.decision == decision)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                SamBid.title.ilike(like),
                SamBid.notice_id.ilike(like),
                SamBid.naics_code.ilike(like),
            )
        )
    try:
        rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="Database unavailable — check DATABASE_URL in server/.env"
        ) from exc
    return {"bids": [_bid_to_dict(b) for b in rows], "count": len(rows)}


@router.post("/evaluate")
def evaluate_bid_endpoint(body: EvaluateRequest) -> dict:
    """Run the NAICS-first evaluator on an ad-hoc bid (mirrors /evaluate-sam)."""
    result = evaluate(
        body.bid_id, body.full_text,
        naics_code=body.naics_code or "", title=body.title or "",
    )
    return result
