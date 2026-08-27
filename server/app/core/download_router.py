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

from app.core import checkpoints, exports, jobs, live, run_logs, run_manager

router = APIRouter(tags=["downloads"])

# What the Active Jobs panel needs per run. Deliberately not the whole run dict:
# that carries every scraped bid, which is megabytes on a large run and is
# already served per portal to the panel that actually shows rows.
_JOB_FIELDS = (
    "run_id", "scraper", "status", "step", "started_at", "finished_at",
    "bids_found", "bids_processed", "documents_downloaded", "queue_position",
    # Where a paused run is holding, so the console can show it without a
    # second request per job.
    "paused_at", "resumed_at",
    # How each portal names the thing it is working on, for the job's subtitle.
    "label", "search", "account_label", "niche_label", "filter_label", "module",
)


def _job(run: dict) -> dict:
    """One row of the jobs list."""
    job = {field: run.get(field) for field in _JOB_FIELDS}
    job["errors"] = len(run.get("errors") or [])
    job["warnings"] = len(run.get("warnings") or [])
    job["log_seq"] = run_logs.latest_seq(run.get("run_id", ""))
    return job


@router.get("/runs")
def list_all_runs(active: bool = False, portal: str | None = None, limit: int = 50) -> dict:
    """Every portal's runs in one call — what the Active Jobs panel polls.

    `active=true` narrows to the ones still going (queued or running), which is
    the panel's normal mode: one request covers every portal, so the panel can
    live in the console's layout and keep reporting while the user moves
    between portals.
    """
    runs = run_manager.list_runs(portal)
    if active:
        runs = [r for r in runs if r.get("status") not in run_manager.TERMINAL_STATUSES]
    return {"jobs": [_job(r) for r in runs[:limit]], "capacity": jobs.stats()}


@router.get("/runs/{run_id}/logs")
def run_log_tail(run_id: str, after: int = 0) -> dict:
    """This run's recent log lines, newer than `after`.

    The panel passes back the last `seq` it saw, so each poll carries only what
    has happened since — see app/core/run_logs.
    """
    if not run_manager.get_run(run_id):
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    lines = run_logs.tail(run_id, after=after)
    return {"lines": lines, "seq": lines[-1]["seq"] if lines else after}


@router.get("/runs/{run_id}/screenshot")
def run_screenshot(run_id: str) -> dict:
    """A base64 PNG frame of the run's live browser, or null if none is
    available yet (driver not up, run finished, or capture failed). Always 200
    so the modal's poller stays quiet while it waits for the first frame."""
    return {"screenshot": live.capture(run_id)}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict:
    """Stop an in-flight run, whichever scraper owns it.

    A run still waiting for a slot is cancelled outright — its work is dropped
    before it begins, and no browser is ever started. A run already executing is
    stopped the cooperative way: SAM runs on its own threaded engine with a stop
    event, so those are routed to it; every other scraper is a BaseScraper, where
    locking the run state to "stopped" (run_manager) plus interrupting its
    browser (live.stop) unwinds it cleanly. 409 if the run isn't in progress —
    there is nothing to stop.
    """
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if run.get("status") not in run_manager.STOPPABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Run is not in progress — nothing to stop.")

    # Queued and not yet started: drop it from the pool. request_stop below then
    # records the terminal status, so a cancelled job reads as "stopped" like
    # any other — the user asked for it to not run, and it did not run.
    cancelled = jobs.cancel(run_id)
    if cancelled:
        run_manager.request_stop(run_id)
        return {"stopped": True, "run_id": run_id, "cancelled_before_start": True}

    if run.get("scraper") == "sam":
        # Lazy import to avoid a heavy engine import at module load.
        from app.scrapers.sam import runner as sam_runner

        sam_runner.request_stop(run_id)
    else:
        run_manager.request_stop(run_id)
        live.stop(run_id)
    return {"stopped": True, "run_id": run_id, "cancelled_before_start": False}


@router.post("/runs/{run_id}/pause")
def pause_run(run_id: str) -> dict:
    """Park an executing run at its next checkpoint.

    The run keeps its browser and its slot; what it gives up is the network and
    the CPU. That is the trade someone makes when they pause a long SAM sweep to
    get a SEPTA delivery out, and it is what makes the resume exact — the worker
    is still standing where it stopped, so it carries on at the next record with
    nothing replayed and nothing collected twice.

    409 if the run is not executing: a queued run is already not consuming
    anything, and a finished one has nothing to hold.
    """
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if not run_manager.request_pause(run_id):
        raise HTTPException(
            status_code=409,
            detail="Only a running job can be paused — this one is "
                   f"{run.get('status') or 'not running'}.",
        )
    mark = checkpoints.get(run_id)
    return {
        "paused": True,
        "run_id": run_id,
        # The worker parks at its next checkpoint, which is between records
        # rather than instantly — the console shows "pausing…" until its own
        # poll sees the status settle.
        "checkpoint": mark.summary() if mark else None,
    }


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str) -> dict:
    """Release a parked run. It continues from the record after its last."""
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if not run_manager.request_resume(run_id):
        raise HTTPException(
            status_code=409,
            detail="This job is not paused — nothing to resume.",
        )
    mark = checkpoints.get(run_id)
    return {
        "resumed": True,
        "run_id": run_id,
        "checkpoint": mark.summary() if mark else None,
    }


@router.get("/runs/{run_id}/download")
def download_run(run_id: str):
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if run.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Run has not completed — nothing to download yet.")

    # Runs whose only output is the spreadsheet serve it unwrapped — there is
    # nothing else a ZIP would carry. Checked BEFORE zip_path so a run archived
    # while the portal still packaged ZIPs is delivered as a bare sheet too;
    # otherwise every run made before the switch would keep downloading as a ZIP
    # forever, which reads as "the change didn't work".
    if exports.is_excel_only(run):
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


@router.get("/runs/{run_id}/download/attachments")
def download_run_attachments(run_id: str):
    """Just the bid documents, as one ZIP — the archive minus its summary sheet.

    A second view of the artifact `/download` already serves, not a second
    artifact: it is cut from the same archive on demand, so nothing extra is
    kept on disk and a run downloaded twice cannot hand back two different sets
    of files.

    A run that downloaded nothing is a 404 with a reason, not an empty ZIP. An
    archive that opens onto nothing reads as a broken download; being told the
    run found no attachments is the actual answer.
    """
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if run.get("status") != "completed":
        raise HTTPException(
            status_code=409, detail="Run has not completed — nothing to download yet."
        )
    if not exports.attachments_supported(run):
        raise HTTPException(
            status_code=404,
            detail="This portal's runs do not package attachments separately.",
        )

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)
    try:
        count = exports.build_attachments_zip(run, tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    if not count:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=404,
            detail="This run downloaded no bid attachments — its results are the summary sheet.",
        )

    return FileResponse(
        str(tmp_path),
        filename=exports.attachments_zip_name(run),
        media_type="application/zip",
        # Deleted once the response has streamed: the ZIP exists for this
        # download and nothing else, and a temp file per click is exactly the
        # disk bloat the archive layout exists to avoid.
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )


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
