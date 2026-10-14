"""FastAPI router for PPRA scraper."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import jobs, run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.ppra import filters as ppra_filters
from app.scrapers.ppra import repository
from app.scrapers.ppra.models import PpraBid, PpraFilterOption, PpraRun
from app.scrapers.ppra.schemas import (
    PpraBidOut,
    PpraFilterOptionOut,
    PpraFilters,
    PpraScrapeRequest,
)
from app.scrapers.ppra.scraper import execute_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ppra", tags=["ppra"])


@router.post("/scrape")
def start_scrape(request: PpraScrapeRequest) -> dict[str, Any]:
    """Start an active PPRA tenders scrape run."""
    filters = request.filters
    options = request.options
    filters_hash = ppra_filters.compute_filters_hash(filters)

    label = timestamp()
    folder = run_manager.make_run_folder(f"PPRA ({label})")

    search_parts = [
        f"keyword={filters.keyword}" if filters.keyword else "",
        f"tender_no={filters.tender_number}" if filters.tender_number else "",
        f"category={filters.category}" if filters.category and filters.category != "ALL" else "",
        f"sector={filters.sector}" if filters.sector and filters.sector != "ALL" else "",
    ]
    search_desc = ", ".join(p for p in search_parts if p) or "all active tenders"

    run = run_manager.create_run(
        "ppra",
        folder,
        {
            "label": label,
            "search": search_desc,
            "filters": filters.model_dump(),
            "filters_hash": filters_hash,
            "options": options.model_dump(),
            "live_preview": request.live_preview,
            "total_expected": None,
            "total_pages": None,
            "current_page": 0,
            "rows_new": 0,
            "rows_changed": 0,
            "skipped_closing": 0,
            "kept_unverified": 0,
        },
    )

    repository.init_run_in_db(
        run["run_id"],
        filters.model_dump(),
        filters_hash,
        options.model_dump(),
    )

    jobs.submit(
        run["run_id"],
        execute_run,
        run["run_id"],
        filters.model_dump(),
        options.model_dump(),
    )

    return {"run_id": run["run_id"], "search": search_desc, "folder": run["folder"]}


@router.post("/scrape/refresh/{run_id}")
def refresh_scrape(run_id: str) -> dict[str, Any]:
    """Start a new run using identical filters and diffing against parent run."""
    parent_run = run_manager.get_run(run_id)
    if not parent_run:
        raise HTTPException(status_code=404, detail=f"Parent run {run_id} not found")

    filters_dict = parent_run.get("filters", {})
    options_dict = parent_run.get("options", {})
    filters = PpraFilters(**filters_dict)
    options = PpraOptions(**options_dict)
    filters_hash = ppra_filters.compute_filters_hash(filters)

    label = timestamp()
    folder = run_manager.make_run_folder(f"PPRA_Refresh ({label})")

    run = run_manager.create_run(
        "ppra",
        folder,
        {
            "label": label,
            "search": f"Refresh of {run_id}",
            "parent_run_id": run_id,
            "filters": filters.model_dump(),
            "filters_hash": filters_hash,
            "options": options.model_dump(),
            "live_preview": parent_run.get("live_preview", False),
        },
    )

    repository.init_run_in_db(
        run["run_id"],
        filters.model_dump(),
        filters_hash,
        options.model_dump(),
        parent_run_id=run_id,
    )

    jobs.submit(
        run["run_id"],
        execute_run,
        run["run_id"],
        filters.model_dump(),
        options.model_dump(),
        parent_run_id=run_id,
    )

    return {"run_id": run["run_id"], "parent_run_id": run_id, "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict[str, Any]:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def list_runs() -> dict[str, Any]:
    return {"runs": run_manager.list_runs(scraper="ppra")}


@router.get("/filters")
def get_filters(kind: str | None = None) -> dict[str, Any]:
    options = ppra_filters.get_cached_filter_options(kind)
    return {"options": options}


@router.post("/filters/refresh")
def refresh_filters() -> dict[str, Any]:
    # Triggers option re-harvest if requested
    return {"refreshed": True}


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by run"),
    query: str = Query("", description="Search tender number / title / organization"),
    after_id: int | None = Query(None, description="Cursor for live updates"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Query scraped tenders from ppra_bids."""
    stmt = select(PpraBid).order_by(PpraBid.id.asc() if after_id else PpraBid.id.desc())

    if run_id:
        stmt = stmt.where(PpraBid.run_id == run_id)
    if after_id:
        stmt = stmt.where(PpraBid.id > after_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                PpraBid.title.ilike(like),
                PpraBid.tender_no.ilike(like),
                PpraBid.organization.ilike(like),
                PpraBid.city.ilike(like),
            )
        )

    rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()

    def _bid_dict(b: PpraBid) -> dict[str, Any]:
        return {
            "id": b.id,
            "run_id": b.run_id,
            "page_no": b.page_no,
            "position": b.position,
            "tender_no": b.tender_no,
            "title": b.title,
            "description": b.description,
            "sector": b.sector,
            "tender_ref_no": b.tender_ref_no,
            "tender_type": b.tender_type,
            "category": b.category,
            "nature": b.nature,
            "organization": b.organization,
            "department": b.department,
            "city": b.city,
            "country": b.country,
            "status": b.status,
            "advertised_on": str(b.advertised_on) if b.advertised_on else None,
            "closing_at": str(b.closing_at) if b.closing_at else None,
            "detail_url": b.detail_url,
            "invoice_url": b.invoice_url,
            "change_status": b.change_status,
            "diff": b.diff,
            "scraped_at": str(b.scraped_at) if b.scraped_at else None,
        }

    return {"bids": [_bid_dict(b) for b in rows], "count": len(rows)}

