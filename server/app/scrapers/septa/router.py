from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import run_manager
from app.core.filenames import timestamp
from app.db import get_session
from app.scrapers.septa import niches as niche_catalog
from app.scrapers.septa.models import EXCEL_COLUMNS, SeptaBid
from app.scrapers.septa.scraper import execute_run

router = APIRouter(prefix="/septa", tags=["septa"])


class ScrapeRequest(BaseModel):
    # `niche` is what the UI sends: the scraper then searches every keyword and
    # every commodity code that niche owns, one search per term, and merges the
    # results into a single deduplicated sheet.
    #
    # The three single-term filters below predate niches and are still accepted
    # so an ad-hoc one-off search is possible via the API. They combine freely
    # with a niche (a date narrows every one of the niche's searches); a request
    # with none of them means today's open quotes, the portal's own default.
    #   niche          — catalog key from GET /septa/niches
    #   date_filter    — YYYY-MM-DD "opens on" date
    #   keyword        — free-text keyword search
    #   commodity_code — SEPTA commodity code
    niche: str | None = None
    date_filter: str | None = None
    keyword: str | None = None
    commodity_code: str | None = None


@router.get("/niches")
def list_niches(session: Session = Depends(get_session)) -> dict:
    """The niche catalog that drives the UI dropdown.

    Seeded from app/scrapers/septa/niches.py at startup. Each entry carries its
    full term list plus the counts the panel shows ("12 keywords, 8 codes").
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
                "keywords": [k.term for k in sorted(niche.keywords, key=lambda k: (k.sort_order, k.id))],
                "codes": [c.code for c in sorted(niche.codes, key=lambda c: (c.sort_order, c.id))],
                "keyword_count": len(niche.keywords),
                "code_count": len(niche.codes),
            }
            for niche in rows
        ]
    }


@router.post("/scrape")
def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks, live_preview: bool = False) -> dict:
    niche = (request.niche or "").strip() or None
    date_filter = (request.date_filter or "").strip() or None
    keyword = (request.keyword or "").strip() or None
    commodity_code = (request.commodity_code or "").strip() or None

    # Validate the niche before the run exists, so a bad key is an immediate 400
    # rather than a run that starts a browser and then fails.
    niche_label = None
    if niche:
        terms = niche_catalog.niche_terms(niche)
        if terms is None:
            raise HTTPException(status_code=400, detail=f"Unknown SEPTA niche: {niche}")
        niche_label, keywords, codes = terms
        if not keywords and not codes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Niche '{niche_label}' has no keywords or commodity codes configured — "
                    "add them to server/app/scrapers/septa/niches.py and restart."
                ),
            )

    search = ", ".join(
        part for part in (
            f"niche={niche_label}" if niche_label else "",
            f"date={date_filter}" if date_filter else "",
            f"keyword={keyword}" if keyword else "",
            f"commodity={commodity_code}" if commodity_code else "",
        ) if part
    ) or "today's open quotes"

    label = timestamp()  # e.g. 2026-07-20 14-30-05
    # Per-run workspace folder. Timestamped so concurrent runs never share a
    # workspace — each is packaged and deleted independently on completion.
    # A SEPTA run's only output is the spreadsheet, so nothing else lands here.
    folder = run_manager.make_run_folder(f"Septa ({label})")
    run = run_manager.create_run(
        "septa",
        folder,
        {
            "label": label,
            "search": search,
            "niche": niche,
            "niche_label": niche_label,
            "date_filter": date_filter,
            "keyword": keyword,
            "commodity_code": commodity_code,
            "excel_exported": False,
            "live_preview": live_preview,
        },
    )
    background_tasks.add_task(
        execute_run, run["run_id"], date_filter, keyword, commodity_code, niche
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
    return {"runs": run_manager.list_runs(scraper="septa")}


def _bid_to_dict(bid: SeptaBid) -> dict:
    data = {attr: getattr(bid, attr) for attr, _ in EXCEL_COLUMNS}
    data.update(id=bid.id, run_id=bid.run_id)
    return data


@router.get("/bids")
def list_bids(
    run_id: str | None = Query(None, description="Filter by scrape run"),
    query: str = Query("", description="Search requisition number / summary"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    """Return SEPTA quotes stored in the database, most recent first."""
    stmt = select(SeptaBid).order_by(SeptaBid.scraped_at.desc(), SeptaBid.id.desc())
    if run_id:
        stmt = stmt.where(SeptaBid.run_id == run_id)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                SeptaBid.requisition_number.ilike(like),
                SeptaBid.summary.ilike(like),
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
