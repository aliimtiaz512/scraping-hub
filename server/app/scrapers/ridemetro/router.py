from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import jobs, run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.ridemetro import accounts
from app.scrapers.ridemetro.models import API_FIELDS, RideMetroBid, RideMetroRun
from app.scrapers.ridemetro.scraper import execute_run

router = APIRouter(prefix="/ridemetro", tags=["ridemetro"])


@router.get("/accounts")
def list_accounts() -> dict:
    """The logins a run can use, for the console's account picker.

    Each carries its label and whether it is configured — no login address — so
    an account with no credentials in `.env` is shown as unavailable rather than
    offered as a button that would fail.
    """
    return {"accounts": accounts.catalog(), "default": accounts.DEFAULT_ACCOUNT}


@router.post("/scrape")
def start_scrape(
    account: str = Query(
        accounts.DEFAULT_ACCOUNT,
        description="Which login to run as: hoope_lab | fedpints",
    ),
    live_preview: bool = False,
) -> dict:
    # Resolved before the run exists so a bad or unconfigured account is a
    # straight 400 the console can show against the picker, rather than a run
    # that appears, starts a browser, and fails at the login screen.
    try:
        selected = accounts.require(account)
    except accounts.UnknownAccount as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except accounts.AccountNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    label = timestamp()  # e.g. 2026-07-08 14-30-05
    # Per-run workspace folder; its name becomes the run's archive name, so the
    # account is in it — two accounts sweep different networks and their reports
    # should not be told apart only by timestamp. Timestamped so concurrent runs
    # never share a workspace.
    folder = run_manager.make_run_folder(f"RideMetro {selected.label} ({label})")
    run = run_manager.create_run(
        "ridemetro",
        folder,
        # The account's key and label only — the run state is served to the
        # console, and the login address has no business being there.
        {
            "label": label,
            "live_preview": live_preview,
            "account": selected.key,
            "account_label": selected.label,
        },
    )
    jobs.submit(run["run_id"], execute_run, run["run_id"])
    return {"run_id": run["run_id"], "folder": run["folder"], "account": selected.key}


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
    data = {attr: getattr(bid, attr) for attr in API_FIELDS}
    data.update(id=bid.id, run_id=bid.run_id, raw_data=bid.raw_data)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    agency: str | None = Query(None, description="Filter by agency name"),
    account: str | None = Query(None, description="Filter by the login the run used"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return RideMetro opportunities stored in the database, most recent first."""
    stmt = select(RideMetroBid).order_by(RideMetroBid.scraped_at.desc(), RideMetroBid.id.desc())
    if run_id:
        stmt = stmt.where(RideMetroBid.run_id == run_id)
    if agency:
        stmt = stmt.where(RideMetroBid.agency == agency)
    if account:
        # The account lives on the run row, so this is a join through it — the
        # two accounts sweep different networks, and "what did Fedpints see"
        # is the natural question once both are in the same table.
        stmt = stmt.where(
            RideMetroBid.run_id.in_(
                select(RideMetroRun.run_id).where(RideMetroRun.account == account)
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
