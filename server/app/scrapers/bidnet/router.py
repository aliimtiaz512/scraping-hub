import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.bidnet import export, filters
from app.scrapers.bidnet.discovery import execute_discovery
from app.scrapers.bidnet.filters import SidebarFilterRequest
from app.scrapers.bidnet.keywords import (
    MAX_KEYWORDS,
    MAX_KEYWORD_LENGTH,
    catalog_terms,
    clean_keywords,
    validate_keywords,
)
from app.scrapers.bidnet.models import EXCEL_COLUMNS, BidnetBid
from app.scrapers.bidnet.scraper import execute_run

router = APIRouter(prefix="/bidnet", tags=["bidnet"])


class ScrapeRequest(BaseModel):
    """What a run searches, and how it is narrowed.

    `keywords` are typed in the frontend and go into the portal's search box one
    at a time — each is a separate search, and each may be a whole boolean
    expression (`Construction AND Demolition`, `(A AND B) OR (A AND C)`), which
    the box supports. Leaving them empty falls back to the server-side catalog in
    `keywords.py`.
    """

    keywords: list[str] = Field(default_factory=list)
    filters: SidebarFilterRequest = Field(default_factory=SidebarFilterRequest)


@router.get("/filters")
def filter_catalog() -> dict:
    """Every sidebar filter the frontend can offer, with its selectable options.

    Options are the seeded catalog merged with whatever the last discovery pass
    harvested; `discovered_at` is null until one has run, and the sections whose
    seed is only the sidebar's inline slice are flagged `partial`.
    """
    return filters.catalog()


@router.post("/filters/refresh")
def refresh_filters(background_tasks: BackgroundTasks, live_preview: bool = False) -> dict:
    """Re-harvest the full option lists from the portal's "View All" dialogs.

    Runs as a normal background run (poll it on /scrape/status/{run_id}) because
    it logs into BidNet and drives a browser, same as a scrape.
    """
    folder = run_manager.make_run_folder(f"Bidnetdirect filters ({timestamp()})")
    run = run_manager.create_run(
        "bidnet",
        folder,
        {"label": "filter options", "keyword": "filter option discovery", "live_preview": live_preview},
    )
    background_tasks.add_task(execute_discovery, run["run_id"])
    return {"run_id": run["run_id"]}


@router.get("/keyword-limits")
def keyword_limits() -> dict:
    """The portal's own search-box limits, so the UI can enforce them as you type."""
    return {"max_keywords": MAX_KEYWORDS, "max_keyword_length": MAX_KEYWORD_LENGTH}


@router.post("/scrape")
def start_scrape(
    background_tasks: BackgroundTasks,
    request: ScrapeRequest | None = None,
    live_preview: bool = False,
) -> dict:
    # Sidebar filters are optional: an omitted body means the portal's own
    # defaults (Open Solicitations, every purchasing group, nothing else set).
    # Checked before the keywords so a bad request is reported as such rather
    # than being masked by an unrelated server-side configuration error.
    body = request or ScrapeRequest()
    problems = filters.validate_request(body.filters)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))

    # Keywords typed in the frontend win; an empty list falls back to the
    # server-side catalog, so an automated caller can still run the curated set.
    keywords = clean_keywords(body.keywords) or clean_keywords(catalog_terms())
    problems = validate_keywords(keywords)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))
    if not keywords:
        raise HTTPException(
            status_code=400,
            detail=(
                "no keywords to search — enter at least one in the console, or add "
                "them to NICHES in app/scrapers/bidnet/keywords.py"
            ),
        )

    sidebar = body.filters
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
            "live_preview": live_preview,
            "filters": sidebar.model_dump(exclude_none=True),
            "filters_summary": sidebar.summary(),
        },
    )
    background_tasks.add_task(execute_run, run["run_id"], keywords, sidebar)
    return {
        "run_id": run["run_id"],
        "keywords": keywords,
        "folder": run["folder"],
        "filters": sidebar.model_dump(exclude_none=True),
    }


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
