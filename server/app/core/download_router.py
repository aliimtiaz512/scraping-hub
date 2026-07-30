"""Shared per-run endpoints:

* ``GET /runs/{run_id}/download`` — the run's archive ZIP (cumulative Excel +
  all bid documents, built by exports.archive_run when the run completed). For
  runs from before the archive existed — or a run whose packaging failed — it
  falls back to packaging the run's on-disk folder on demand, or a bare Excel.
* ``GET /runs/{run_id}/screenshot`` — a live frame of the run's browser (any
  portal), for the Live Preview modal. Returns null until a frame is available.
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.core import exports, live, run_manager

router = APIRouter(tags=["downloads"])


@router.get("/runs/{run_id}/screenshot")
def run_screenshot(run_id: str) -> dict:
    """A base64 PNG frame of the run's live browser, or null if none is
    available yet (driver not up, run finished, or capture failed). Always 200
    so the modal's poller stays quiet while it waits for the first frame."""
    return {"screenshot": live.capture(run_id)}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict:
    """Stop an in-flight run, whichever scraper owns it.

    SAM runs on its own threaded engine with a cooperative stop event, so those
    are routed to it. Every other scraper is a BaseScraper: locking its run state
    to "stopped" (run_manager) plus interrupting its browser (live.stop) unwinds
    it cleanly. 409 if the run isn't in progress — there is nothing to stop.
    """
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if run.get("status") not in ("pending", "running"):
        raise HTTPException(status_code=409, detail="Run is not in progress — nothing to stop.")

    if run.get("scraper") == "sam":
        # Lazy import to avoid a heavy engine import at module load.
        from app.scrapers.sam import runner as sam_runner

        sam_runner.request_stop(run_id)
    else:
        run_manager.request_stop(run_id)
        live.stop(run_id)
    return {"stopped": True, "run_id": run_id}


@router.get("/runs/{run_id}/download")
def download_run(run_id: str):
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if run.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Run has not completed — nothing to download yet.")

    # Portals whose only output is the spreadsheet serve it unwrapped — there is
    # nothing else a ZIP would carry. Checked BEFORE zip_path so a run archived
    # while the portal still packaged ZIPs is delivered as a bare sheet too;
    # otherwise every run made before the switch would keep downloading as a ZIP
    # forever, which reads as "the change didn't work".
    if run.get("scraper") in exports.EXCEL_ONLY_PORTALS:
        return _excel_response(run)

    # The normal path: the archive ZIP packaged at completion.
    zip_path = run.get("zip_path")
    if zip_path and Path(zip_path).is_file():
        return FileResponse(
            zip_path,
            filename=Path(zip_path).name,
            media_type="application/zip",
        )

    # Legacy fallbacks below: runs made before the archive existed (their files
    # may still sit in data/documents), or a run whose packaging failed.
    if run.get("scraper") in exports.DOC_PORTALS or Path(run.get("folder") or "").is_dir():
        # Build the ZIP into a temp file (documents can be large) and delete it
        # after the response has streamed.
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp.close()
        tmp_path = Path(tmp.name)
        try:
            filename = exports.build_zip(run, tmp_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return FileResponse(
            str(tmp_path),
            filename=filename,
            media_type="application/zip",
            background=BackgroundTask(tmp_path.unlink, missing_ok=True),
        )

    return _excel_response(run)


def _excel_response(run: dict) -> Response:
    """The run's sheet as a download — regenerated from the DB when possible,
    else read from its archived/on-disk copy."""
    payload = exports.excel_bytes(run)
    if not payload:
        raise HTTPException(status_code=404, detail="No results are available for this run.")
    data, filename = payload
    return Response(
        content=data,
        media_type=exports.XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
