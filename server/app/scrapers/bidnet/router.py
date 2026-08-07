import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import jobs, run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.bidnet import export, filters, niches as niche_catalog, storage
from app.scrapers.bidnet.discovery import execute_discovery
from app.scrapers.bidnet.filters import SidebarFilterRequest
from app.scrapers.bidnet.models import EXCEL_COLUMNS, BidnetBid
from app.scrapers.bidnet.scraper import execute_run

router = APIRouter(prefix="/bidnet", tags=["bidnet"])


class ScrapeRequest(BaseModel):
    """What a run searches, and how it is narrowed.

    `niche` is a catalog key from `GET /bidnet/niches`. Its keywords live in the
    database and are resolved server-side — the client never sends search terms,
    and never sees them.
    """

    niche: str
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
def refresh_filters(live_preview: bool = False) -> dict:
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
    jobs.submit(run["run_id"], execute_discovery, run["run_id"])
    return {"run_id": run["run_id"]}


@router.get("/niches")
def list_niches(session: Session = Depends(get_session)) -> dict:
    """The niche dropdown: key, label and how many keywords each one searches.

    Seeded from app/scrapers/bidnet/niches.py at startup. The keywords
    themselves are deliberately **not** returned — a niche is the only search
    input the frontend has, and the terms stay server-side.
    """
    try:
        rows = niche_catalog.list_niches(session)
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — check DATABASE_URL in server/.env",
        ) from exc
    return {
        "niches": [
            {
                "key": niche.key,
                "label": niche.label,
                "slug": niche.slug,
                "keyword_count": len(niche.keywords),
            }
            for niche in rows
        ]
    }


@router.post("/scrape")
def start_scrape(
    request: ScrapeRequest,
    live_preview: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    # Sidebar filters are optional: an omitted `filters` means the portal's own
    # defaults (Open Solicitations, every purchasing group, nothing else set).
    problems = filters.validate_request(request.filters)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))

    try:
        niche = niche_catalog.get_niche(session, request.niche)
        keywords = niche_catalog.keywords_for(session, request.niche) if niche else []
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable — check DATABASE_URL in server/.env",
        ) from exc
    if niche is None:
        raise HTTPException(status_code=400, detail=f"Unknown niche: {request.niche}")
    if not keywords:
        raise HTTPException(
            status_code=400,
            detail=(
                f"niche '{niche.label}' has no keywords — add them to NICHES in "
                "app/scrapers/bidnet/niches.py and restart the API"
            ),
        )

    sidebar = request.filters
    label = timestamp()  # e.g. 2026-07-08 14-30-05
    # The run writes into its niche's folder inside the day's session root, so a
    # day's niches collect side by side and the archive can bundle all of them.
    # Re-running a niche reuses its folder rather than making a second one.
    root = storage.session_root()
    folder = storage.niche_folder(root, niche.label, niche.key, niche.slug)
    run = run_manager.create_run(
        "bidnet",
        folder,
        {
            "label": label,
            "niche": niche.key,
            "niche_label": niche.label,
            "niche_slug": niche.slug,
            # The day's bundle this run belongs to. `archive_run` zips this
            # whole root, not just `folder`, so the download holds every niche
            # run so far — see app/scrapers/bidnet/storage.py.
            "session_root": str(root),
            "niche_folder": str(folder),
            # Names the run's spreadsheet (see core.exports.excel_name).
            "search": niche.label,
            # The keyword currently being searched; seeded with the first so the
            # status panel has something to show before the browser is up.
            "keyword": keywords[0],
            "keyword_count": len(keywords),
            "excel_exported": False,
            "live_preview": live_preview,
            "filters": sidebar.model_dump(exclude_none=True),
            "filters_summary": sidebar.summary(),
        },
    )
    jobs.submit(run["run_id"], execute_run, run["run_id"], niche.key, sidebar)
    return {
        "run_id": run["run_id"],
        "niche": niche.key,
        "niche_label": niche.label,
        "keyword_count": len(keywords),
        "folder": run["folder"],
        "session_root": run["session_root"],
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
