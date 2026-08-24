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
from app.scrapers.myflorida import accounts, dates
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
    # Which vendor login to run as. Blank keeps the account a run used before
    # there was a choice, so an existing caller is unaffected.
    account: str = accounts.DEFAULT_ACCOUNT
    # The posting-date window, `yyyy-mm-dd` (`mm/dd/yyyy` is also accepted).
    # Either end may stand alone; both omitted means every posting date, which
    # is what every caller written before this field got. Applies to both search
    # modes — it belongs to the search form, not to what is typed into it.
    start_date: str | None = None
    end_date: str | None = None


@router.get("/accounts")
def list_accounts() -> dict:
    """The logins a run can use, for the console's account picker.

    Served rather than hardcoded in the dashboard so that which accounts exist
    and which are usable stays the server's to say — an account with no
    credentials in `.env` is shown as unavailable rather than offered as a
    button that fails at the login form.
    """
    return {"accounts": accounts.catalog(), "default": accounts.DEFAULT_ACCOUNT}


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
def start_scrape(request: ScrapeRequest, live_preview: bool = False) -> dict:
    if request.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category: {request.category}")
    if request.mode not in SEARCH_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown search mode: {request.mode}")
    # Resolved before the run exists so a bad or unconfigured account is a 400
    # on the button rather than a run that opens a visible browser and fails at
    # the login form with someone sitting there waiting to type an OTP.
    try:
        selected = accounts.require(request.account)
    except accounts.UnknownAccount as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except accounts.AccountNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # De-duplicate while preserving order; an empty list means "no status filter".
    ad_statuses = list(dict.fromkeys(request.ad_statuses))
    unknown = [s for s in ad_statuses if s not in AD_STATUS_OPTIONS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown ad status: {', '.join(unknown)}")
    ad_types = list(dict.fromkeys(request.ad_types))
    unknown_types = [t for t in ad_types if t not in AD_TYPE_OPTIONS]
    if unknown_types:
        raise HTTPException(status_code=400, detail=f"Unknown ad type: {', '.join(unknown_types)}")
    # Rejected here rather than at the form: an unreadable or inverted window is
    # a 400 on the button, not a run that opens a browser, waits for someone to
    # type a one-time password, and then searches the wrong thing.
    try:
        window = dates.parse(request.start_date, request.end_date)
    except dates.DateRangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
            # Key and label only — the run state is served to the console, and
            # the login address has no business on a screen.
            "account": selected.key,
            "account_label": selected.label,
            # The window this run was launched with, on the record whether or
            # not the portal has been asked for it yet — a run's own account of
            # what it searched has to survive the flag below being flipped.
            "start_date": window.isoformat()[0],
            "end_date": window.isoformat()[1],
            "date_range_summary": window.describe(),
            "date_filter_ready": dates.PORTAL_DATE_FILTER_READY,
        },
    )
    jobs.submit(
        run["run_id"], execute_run, run["run_id"], codes, ad_statuses, ad_types, keywords, window
    )
    return {
        "run_id": run["run_id"],
        "mode": request.mode,
        "codes": codes,
        "keywords": keywords,
        "folder": run["folder"],
        "account": selected.key,
        "start_date": window.isoformat()[0],
        "end_date": window.isoformat()[1],
        "date_range_summary": window.describe(),
        # False while the portal injection is still outstanding, so the console
        # can say so rather than implying a window that is already in force.
        "date_filter_ready": dates.PORTAL_DATE_FILTER_READY,
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
