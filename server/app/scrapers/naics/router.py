"""NAICS reference tool routes — list, search, and a refresh scrape."""

import base64
import binascii

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import jobs, run_manager
from app.db import get_session
from app.scrapers.naics import importer, runner
from app.scrapers.naics.models import NaicsCode

#: A spreadsheet of codes is small — a few hundred rows of six digits. The cap
#: is a guard against a wrong file being posted, not a real limit on the work.
MAX_IMPORT_BYTES = 5 * 1024 * 1024

router = APIRouter(prefix="/naics", tags=["naics"])


class NaicsImportRequest(BaseModel):
    """A spreadsheet, sent as base64 rather than multipart.

    Base64 keeps this an ordinary JSON endpoint: multipart would pull in
    `python-multipart` as a dependency for one route, and these files are a few
    kilobytes of digits — the encoding overhead is not worth a new package.
    """

    filename: str
    #: The file's bytes, base64-encoded. A `data:` prefix from the browser's
    #: FileReader is tolerated so the client can send what it already has.
    content: str


@router.post("/import")
def import_naics(
    request: NaicsImportRequest, session: Session = Depends(get_session)
) -> dict:
    """Read NAICS codes out of an uploaded .csv / .xlsx / .xls.

    Returns the codes to put in the picker, and an account of what was dropped:
    a file of forty-five entries that yields forty codes has to say which five
    it could not use, or the run quietly searches less than it was given.

    Validated against the reference table, which is what lets a five-digit entry
    be expanded to its six-digit children instead of padded into a code that has
    never existed. See `importer` for why padding is wrong for NAICS.
    """
    payload = (request.content or "").strip()
    # A browser's FileReader gives "data:<mime>;base64,<payload>". Split on the
    # first comma wherever it falls: this used to only look in the first 64
    # characters, and an .xlsx MIME type
    # ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    # pushes the comma to position 77 — so the prefix survived, the whole data
    # URL was fed to the decoder, and every .xlsx upload failed with a base64
    # error while .csv and .xls (shorter MIME types) worked.
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    # Some encoders wrap base64 at 76 columns; the decoder wants it unbroken.
    payload = "".join(payload.split())
    try:
        data = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"could not decode the file ({exc})") from exc
    if not data:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB",
        )

    try:
        catalogue = [
            row[0] for row in session.execute(
                select(NaicsCode.code).order_by(NaicsCode.code)
            ).all()
        ]
    except OperationalError:
        # The picker still works without the catalogue; six-digit codes are then
        # taken at face value and short entries cannot be expanded.
        catalogue = []

    try:
        result = importer.parse(data, request.filename, catalogue or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a bad file is a 400, not a 500
        raise HTTPException(
            status_code=400,
            detail=f"could not read {request.filename!r}: {exc.__class__.__name__}",
        ) from exc

    return result.as_dict()


@router.get("")
def list_naics(
    q: str = Query("", description="Search code or title"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    """List NAICS codes (optionally filtered by `q`), paginated and code-ordered."""
    offset = (page - 1) * limit
    base = select(NaicsCode)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(or_(func.lower(NaicsCode.code).like(like), func.lower(NaicsCode.title).like(like)))
    try:
        total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        rows = session.execute(
            base.order_by(NaicsCode.code).offset(offset).limit(limit)
        ).scalars().all()
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="Database unavailable — check DATABASE_URL in server/.env"
        ) from exc
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "results": [{"code": r.code, "title": r.title} for r in rows],
    }


@router.get("/search")
def search_naics(
    q: str = Query("", description="Search code or title"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    """Alias of the list endpoint with a query — kept for API parity."""
    return list_naics(q=q, page=page, limit=limit, session=session)


@router.post("/scrape")
def start_scrape() -> dict:
    """Refresh the NAICS reference table from the source index page."""
    folder = run_manager.make_run_folder("Naics")
    run = run_manager.create_run("naics", folder, {"search": "NAICS reference refresh"})
    jobs.submit(run["run_id"], runner.execute_run, run["run_id"])
    return {"run_id": run["run_id"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="naics")}
