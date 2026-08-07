from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import jobs, run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.unison import filters, runner
from app.scrapers.unison.models import EXCEL_COLUMNS, UnisonRequest

router = APIRouter(prefix="/unison", tags=["unison"])


@router.get("/filters")
def list_filters() -> dict:
    """The portal's Filter By criteria, for the console's dropdown.

    Served rather than hardcoded in the frontend so the option values the engine
    selects on and the labels the user picks from have one source.
    """
    return {"filters": filters.catalog(), "default": filters.DEFAULT_FILTER_ID}


@router.post("/scrape")
def start_scrape(
    filter_id: str = Query(
        filters.DEFAULT_FILTER_ID,
        description="Portal 'Filter By' option value; -1 (Select Criteria) reads the whole listing",
    ),
    live_preview: bool = False,
) -> dict:
    """Start a run.

    The one parameter is the portal's own Filter By criterion. Everything else
    about how a run is narrowed is a property of the scraper (see `filters`),
    not something a caller chooses.
    """
    if not filters.is_valid_filter(filter_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown Filter By value {filter_id!r} — choose one of: "
                f"{', '.join(value for value, _ in filters.PORTAL_FILTERS)}"
            ),
        )
    label = filters.filter_label(filter_id)
    search = "all requests" if filter_id == filters.DEFAULT_FILTER_ID else label

    # Per-run workspace folder (its name becomes the run's ZIP name). Timestamped
    # so concurrent runs never share a workspace — each is zipped and deleted
    # independently on completion.
    folder = run_manager.make_run_folder(f"Unison ({timestamp()})")
    run = run_manager.create_run(
        "unison",
        folder,
        {
            "search": search,
            "excel_exported": False,
            "live_preview": live_preview,
            "filter_id": filter_id,
            "filter_label": label,
            # What the console shows about how this run was narrowed. The
            # keyword and close-date filters remain off for the testing phase.
            "filters_active": filters.describe(filter_id),
            "filters_summary": filters.summary(filter_id),
        },
    )
    jobs.submit(run["run_id"], runner.execute_run, run["run_id"])
    return {
        "run_id": run["run_id"], "search": search,
        "filter_id": filter_id, "folder": run["folder"],
    }


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="unison")}


def _row_to_dict(row: UnisonRequest) -> dict:
    data = {attr: getattr(row, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=row.id, run_id=row.run_id)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search buyer / description / number"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return Unison buyer requests stored in the database, most recent first."""
    stmt = select(UnisonRequest).order_by(UnisonRequest.scraped_at.desc(), UnisonRequest.id.desc())
    if run_id:
        stmt = stmt.where(UnisonRequest.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                UnisonRequest.buyer.ilike(like),
                UnisonRequest.buyer_description.ilike(like),
                UnisonRequest.buyer_number.ilike(like),
            )
        )
    try:
        rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="Database unavailable — check DATABASE_URL in server/.env"
        ) from exc
    return {"bids": [_row_to_dict(r) for r in rows], "count": len(rows)}
