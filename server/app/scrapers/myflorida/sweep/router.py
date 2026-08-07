"""Ad-status sweep endpoints, mounted alongside the niche flow's router.

Prefix `/myflorida/sweep`, run key `myflorida_sweep` — separate from the niche
flow so run history, downloads and the exports page never mix the two.
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import jobs, run_manager
from app.core.filenames import sanitize_filename
from app.db import get_session
from app.scrapers.myflorida.scraper import AD_STATUS_LABELS
from app.scrapers.myflorida.sweep.config import OTHER, get_config, reload_config
from app.scrapers.myflorida.sweep.models import SweepBid
from app.scrapers.myflorida.sweep.scraper import execute_run

router = APIRouter(prefix="/myflorida/sweep", tags=["myflorida-sweep"])

SCRAPER_KEY = "myflorida_sweep"


class SweepRequest(BaseModel):
    # At least one of preview | open | closed | withdrawn. Unlike the niche
    # flow, an empty list is rejected rather than meaning "every status": a
    # sweep with no filter at all is almost never what someone intends, and it
    # is the most expensive run the system can start.
    ad_statuses: list[str] = ["open"]
    # Optional ceiling for trial runs. None = every advertisement found.
    max_bids: int | None = None


@router.get("/niches")
def niches() -> dict:
    """The niche catalogue as configured, for the UI and for sanity-checking
    that a YAML edit loaded."""
    config = get_config()
    return {
        "version": config.version,
        "cross_listing": config.cross_listing,
        "thresholds": config.thresholds,
        "niches": [
            {
                "key": niche.key,
                "label": niche.label,
                "sheet": niche.sheet,
                "order": niche.order,
                "core_terms": len(niche.core_terms),
                "tier_a_codes": len(niche.codes["tier_a"]),
            }
            for niche in config.ordered_niches()
        ],
        "other_sheet": OTHER,
        "ad_statuses": sorted(AD_STATUS_LABELS),
    }


@router.post("/niches/reload")
def reload_niches() -> dict:
    """Re-read mfmp_niches.yaml without restarting the server — the lexicons are
    expected to be edited often (criteria doc §8)."""
    config = reload_config()
    return {"reloaded": True, "version": config.version, "niches": len(config.niches)}


@router.post("/scrape")
def start_scrape(
    request: SweepRequest, live_preview: bool = False
) -> dict:
    statuses = list(dict.fromkeys(request.ad_statuses))
    unknown = [s for s in statuses if s not in AD_STATUS_LABELS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown ad status: {', '.join(unknown)}")
    if not statuses:
        raise HTTPException(
            status_code=400,
            detail="Pick at least one ad status — a sweep with no filter is not supported.",
        )
    if request.max_bids is not None and request.max_bids < 1:
        raise HTTPException(status_code=400, detail="max_bids must be at least 1")

    search = f"status={'/'.join(statuses)}"
    now = datetime.now()
    folder = run_manager.make_run_folder(
        str(Path(f"MyFlorida-Sweep-{now:%Y-%m-%d}")
            / sanitize_filename(f"{now:%H-%M-%S} ({search})", max_length=120))
    )
    run = run_manager.create_run(
        SCRAPER_KEY,
        folder,
        {
            "search": search,
            "ad_statuses": statuses,
            "max_bids": request.max_bids,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    jobs.submit(run["run_id"], execute_run, run["run_id"], statuses, request.max_bids)
    return {"run_id": run["run_id"], "search": search, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper=SCRAPER_KEY)}


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by sweep run"),
    niche: str | None = Query(None, description="Filter by primary niche (N1-N6 or OTHER)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    stmt = select(SweepBid).order_by(SweepBid.scraped_at.desc(), SweepBid.id.desc())
    if run_id:
        stmt = stmt.where(SweepBid.run_id == run_id)
    if niche:
        stmt = stmt.where(SweepBid.primary_niche == niche)
    try:
        rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="Database unavailable — check DATABASE_URL in server/.env"
        ) from exc
    return {
        "bids": [
            {
                "ad_number": b.ad_number,
                "title": b.title,
                "agency": b.agency,
                "primary_niche": b.primary_niche,
                "primary_score": b.primary_score,
                "match_strength": b.match_strength,
                "closest_niche": b.closest_niche,
                "flags": b.flags,
                "document_chars": b.document_chars,
            }
            for b in rows
        ],
        "count": len(rows),
    }
