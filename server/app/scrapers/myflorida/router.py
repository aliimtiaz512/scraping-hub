from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import sanitize_filename
from app.db import get_session
from app.scrapers.myflorida.commodity_codes import CATEGORIES, get_codes, get_keywords
from app.scrapers.myflorida.models import Bid
from app.scrapers.myflorida.scraper import AD_TYPE_LABELS, execute_run

router = APIRouter(prefix="/myflorida", tags=["myflorida"])


AD_STATUS_OPTIONS = {"preview", "open", "closed", "withdrawn"}
AD_TYPE_OPTIONS = set(AD_TYPE_LABELS)
SEARCH_MODES = {"codes", "keywords"}


class ScrapeRequest(BaseModel):
    category: str
    # Which search path the run takes: the niche's commodity codes, or its
    # keywords (searched one at a time). Exactly one per run.
    mode: str = "codes"
    # Subsets of the niche's catalog; empty means "everything in the niche".
    codes: list[str] = []
    keywords: list[str] = []
    # Any of preview | open | closed | withdrawn; empty = no filter (every status).
    ad_statuses: list[str] = []
    # Any key from AD_TYPE_LABELS; empty = no filter (every ad type).
    ad_types: list[str] = []


@router.get("/categories")
def categories() -> dict:
    return {
        "categories": [
            {
                "key": key,
                "label": value["label"],
                "codes": value["codes"],
                "keywords": value["keywords"],
            }
            for key, value in CATEGORIES.items()
        ],
        "search_modes": sorted(SEARCH_MODES),
    }


def _resolve_subset(requested: list[str], available: list[str], name: str) -> list[str]:
    """De-duplicate a requested subset and check it against the niche's catalog.

    An empty request means the whole catalog — the UI starts with everything
    selected, so this is also what a client that omits the field gets.
    """
    subset = list(dict.fromkeys(requested))
    if not subset:
        return list(available)
    unknown = [item for item in subset if item not in available]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"{name} not in this category: {', '.join(unknown)}",
        )
    return subset


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks, live_preview: bool = False) -> dict:
    if request.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category: {request.category}")
    if request.mode not in SEARCH_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown search mode: {request.mode}")
    # De-duplicate while preserving order; an empty list means "no status filter".
    ad_statuses = list(dict.fromkeys(request.ad_statuses))
    unknown = [s for s in ad_statuses if s not in AD_STATUS_OPTIONS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown ad status: {', '.join(unknown)}")
    ad_types = list(dict.fromkeys(request.ad_types))
    unknown_types = [t for t in ad_types if t not in AD_TYPE_OPTIONS]
    if unknown_types:
        raise HTTPException(status_code=400, detail=f"Unknown ad type: {', '.join(unknown_types)}")

    # Only the chosen mode's list is resolved; the other stays empty so the run
    # record shows exactly what was searched.
    codes: list[str] = []
    keywords: list[str] = []
    if request.mode == "codes":
        codes = _resolve_subset(request.codes, get_codes(request.category), "commodity code")
    else:
        keywords = _resolve_subset(request.keywords, get_keywords(request.category), "keyword")

    # Nested, date-bucketed, niche-separated and self-describing:
    #   MyFlorida-<run date>/<niche>/<run timestamp> (<search mode>)/
    # A new top-level folder is created per calendar day the scraper runs, so a
    # run on the 20th and a run on the 21st land in separate day folders. The
    # innermost run folder names the exact date/time and whether the run searched
    # by keyword or by commodity code.
    now = datetime.now()
    date_folder = f"MyFlorida-{now:%Y-%m-%d}"
    niche = sanitize_filename(CATEGORIES[request.category]["label"], max_length=80)
    mode_label = "keyword search" if request.mode == "keywords" else "commodity code search"
    run_name = sanitize_filename(f"{now:%Y-%m-%d_%H-%M-%S} ({mode_label})", max_length=120)
    folder = run_manager.make_run_folder(str(Path(date_folder) / niche / run_name))
    run = run_manager.create_run(
        "myflorida",
        folder,
        {
            "category": request.category,
            "category_label": CATEGORIES[request.category]["label"],
            "mode": request.mode,
            "ad_statuses": ad_statuses,
            "ad_types": ad_types,
            "codes": codes,
            "keywords": keywords,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], codes, ad_statuses, ad_types, keywords)
    return {
        "run_id": run["run_id"],
        "mode": request.mode,
        "codes": codes,
        "keywords": keywords,
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
