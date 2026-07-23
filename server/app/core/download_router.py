"""`GET /runs/{run_id}/download` — the per-run Download button.

Serves the run's archive ZIP (cumulative Excel + all bid documents, built by
exports.archive_run when the run completed). For runs from before the archive
existed — or a run whose packaging failed — it falls back to packaging the
run's on-disk folder on demand, or to a bare DB-regenerated Excel.
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.core import exports, run_manager

router = APIRouter(tags=["downloads"])


@router.get("/runs/{run_id}/download")
def download_run(run_id: str):
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    if run.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Run has not completed — nothing to download yet.")

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

    payload = exports.excel_bytes(run)
    if not payload:
        raise HTTPException(status_code=404, detail="No results are available for this run.")
    data, filename = payload
    return Response(
        content=data,
        media_type=exports.XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
