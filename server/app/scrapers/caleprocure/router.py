"""Cal eProcure API — login milestone.

Only the run lifecycle is wired here so sign-in can be verified end-to-end
(POST /caleprocure/scrape → poll /caleprocure/scrape/status/{run_id}). The
search request body, the `/bids` listing, and the Excel export are added with
the scraping flow, following the SEPTA router.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core import run_manager
from app.core.filenames import timestamp
from app.scrapers.caleprocure.scraper import execute_run

router = APIRouter(prefix="/caleprocure", tags=["caleprocure"])


@router.post("/scrape")
def start_scrape(background_tasks: BackgroundTasks) -> dict:
    """Start a run. For now this signs in and verifies the session; the
    post-login scraping flow is added next."""
    label = timestamp()  # e.g. 2026-07-21 14-30-05
    folder = run_manager.make_run_folder(f"CalEProcure ({label})")
    run = run_manager.create_run("caleprocure", folder, {"label": label})
    background_tasks.add_task(execute_run, run["run_id"])
    return {"run_id": run["run_id"], "folder": run["folder"]}


@router.get("/scrape/status/{run_id}")
def scrape_status(run_id: str) -> dict:
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/scrape/runs")
def scrape_runs() -> dict:
    return {"runs": run_manager.list_runs(scraper="caleprocure")}
